"""ARQ cron sweep that verifies PROPOSED SkillPackVersion rows (M2.4).

Picks up where the M2.7 propose verbs left off. Every 30 minutes the
worker walks the workspaces that have ``evolver.auto_verifier.enabled``
on and runs :func:`app.services.skill_verifier.verify_skill_version`
for each version still sitting in ``state=PROPOSED``.

The orchestration is **per-workspace fail-safe**: an aux LLM blow-up
in one tenant bumps the dedicated breaker
(``verifier:fail:{workspace_id}``) and fast-skips that tenant for the
remainder of the sweep, but never aborts the loop. The breaker key is
deliberately separate from M0.3's ``judge:`` and M2.7's ``evolver:``
buckets so a degraded judge tier doesn't quietly silence the verifier.

Three-strike auto-recovery is delegated to the underlying
:mod:`app.jobs._breaker` primitive: each successful verify resets the
counter, each catastrophic verify bumps it, and the periodic sweep
reads ``is_breaker_open`` before doing any LLM work for the workspace.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import asc, select

from app.db.models.skill_pack_version import (
    SkillPackVersion,
    SkillPackVersionState,
)
from app.db.models.workspace import Workspace
from app.db.session import get_session_factory
from app.jobs._breaker import bump_failure, is_breaker_open, reset_failure
from app.services import audit as audit_svc
from app.services.evolver_config import get_workspace_evolver_config
from app.services.skill_verifier import (
    VerifierBreakerBucket,
    verify_skill_version,
)

log = logging.getLogger(__name__)


# ── Tunables ────────────────────────────────────────────────
# Per-tick caps so a single sweep can't monopolise the worker. The
# version cap is small because each verify_skill_version makes
# ``2 * min_replay_artifacts`` aux LLM calls; even at the default
# min_replay_artifacts=3 a single tick can fan out to 60 LLM calls
# per workspace before bouncing.
_MAX_WORKSPACES_PER_SWEEP: int = 100
_MAX_VERSIONS_PER_WORKSPACE: int = 20
_BREAKER_TRIP_AT: int = 3
_BREAKER_WINDOW_SECONDS: int = 600
_BREAKER_RECOVER_SECONDS: int = 1800


__all__ = [
    "on_skill_verify_job_failed_permanent",
    "verify_proposed_versions_sweep",
]


async def _audit_safe(
    *,
    workspace_id: Any,
    action: str,
    summary: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Audit on a fresh DB session so ARQ frame failures don't poison ours."""
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="skill_pack_version",
                resource_id=None,
                summary=summary,
                metadata=metadata or {},
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("verifier audit %s failed for ws=%s", action, workspace_id)


async def _list_target_workspaces() -> list[Any]:
    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(Workspace.id)
                    .where(Workspace.deleted_at.is_(None))
                    .order_by(Workspace.created_at.asc())
                    .limit(_MAX_WORKSPACES_PER_SWEEP)
                )
            )
            .scalars()
            .all()
        )
    return list(rows)


async def _list_proposed_for_workspace(workspace_id: Any) -> list[SkillPackVersion]:
    factory = get_session_factory()
    async with factory() as db:
        stmt = (
            select(SkillPackVersion)
            .where(
                SkillPackVersion.workspace_id == workspace_id,
                SkillPackVersion.state == SkillPackVersionState.PROPOSED,
            )
            .order_by(asc(SkillPackVersion.created_at))
            .limit(_MAX_VERSIONS_PER_WORKSPACE)
        )
        return list((await db.execute(stmt)).scalars().all())


async def verify_proposed_versions_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """Cron tick: validate every ``state=PROPOSED`` candidate per workspace.

    Returns a small aggregate dict for the ARQ result store + the admin
    debug surface. Counters are *intentionally* per-status — the M2.5
    dispatch handler uses ``versions_accepted`` as a hint for how many
    fresh approvals the operator should expect.
    """
    _ = ctx
    workspaces_swept = 0
    workspaces_skipped_breaker = 0
    workspaces_skipped_disabled = 0
    versions_verified = 0
    versions_accepted = 0
    versions_rejected = 0
    versions_skipped_insufficient = 0
    versions_errored = 0

    workspace_ids = await _list_target_workspaces()
    factory = get_session_factory()

    for workspace_id in workspace_ids:
        # Per-workspace gate 1: evolver auto_verifier on?
        async with factory() as db:
            try:
                config = await get_workspace_evolver_config(db, workspace_id=workspace_id)
            except Exception:  # pragma: no cover
                log.exception(
                    "verifier sweep failed to read evolver config for ws=%s",
                    workspace_id,
                )
                continue
        if not config.auto_verifier.enabled:
            workspaces_skipped_disabled += 1
            continue

        # Per-workspace gate 2: breaker tripped?
        if await is_breaker_open(
            bucket=VerifierBreakerBucket,
            workspace_id=str(workspace_id),
            trip_at=_BREAKER_TRIP_AT,
        ):
            workspaces_skipped_breaker += 1
            await _audit_safe(
                workspace_id=workspace_id,
                action="verifier.breaker_tripped",
                summary="verifier breaker open; sweep skipped workspace",
                metadata={
                    "trip_at": _BREAKER_TRIP_AT,
                    "window_seconds": _BREAKER_WINDOW_SECONDS,
                },
            )
            continue

        proposed_rows = await _list_proposed_for_workspace(workspace_id)
        if not proposed_rows:
            workspaces_swept += 1
            continue

        ws_consecutive_success = 0
        ws_short_circuit = False
        for version_row in proposed_rows:
            if ws_short_circuit:
                break
            try:
                async with factory() as db:
                    result = await verify_skill_version(
                        db,
                        workspace_id=workspace_id,
                        version_id=version_row.id,
                    )
                    await db.commit()
            except Exception as exc:
                log.exception(
                    "verifier crashed for version=%s ws=%s",
                    version_row.id,
                    workspace_id,
                )
                strikes = await bump_failure(
                    bucket=VerifierBreakerBucket,
                    workspace_id=str(workspace_id),
                    window_seconds=_BREAKER_WINDOW_SECONDS,
                    recover_seconds=_BREAKER_RECOVER_SECONDS,
                )
                versions_errored += 1
                await _audit_safe(
                    workspace_id=workspace_id,
                    action="verifier.errored",
                    summary=(
                        f"verifier crashed for version {version_row.id}; "
                        f"strike {strikes}/{_BREAKER_TRIP_AT}"
                    ),
                    metadata={
                        "version_id": str(version_row.id),
                        "pack_id": str(version_row.pack_id),
                        "strikes": int(strikes),
                        "trip_at": _BREAKER_TRIP_AT,
                        "error_class": type(exc).__name__,
                    },
                )
                if strikes >= _BREAKER_TRIP_AT:
                    await _audit_safe(
                        workspace_id=workspace_id,
                        action="verifier.breaker_tripped",
                        summary=(
                            f"verifier breaker opened for ws={workspace_id} "
                            f"after {strikes} consecutive failures"
                        ),
                        metadata={
                            "strikes": int(strikes),
                            "trip_at": _BREAKER_TRIP_AT,
                            "recover_seconds": _BREAKER_RECOVER_SECONDS,
                        },
                    )
                    ws_short_circuit = True
                ws_consecutive_success = 0
                continue

            versions_verified += 1
            if result.status == "accepted":
                versions_accepted += 1
            elif result.status == "rejected":
                versions_rejected += 1
            elif result.status == "skipped_insufficient":
                versions_skipped_insufficient += 1
                versions_accepted += 1
            elif result.status == "errored":
                versions_errored += 1

            if result.status in {"accepted", "rejected", "skipped_insufficient"}:
                ws_consecutive_success += 1
                if ws_consecutive_success >= _BREAKER_TRIP_AT:
                    await reset_failure(
                        bucket=VerifierBreakerBucket,
                        workspace_id=str(workspace_id),
                    )
            else:
                # ``status=errored`` returned cleanly counts as a soft
                # failure. Accumulate so a workspace whose aux model is
                # mis-resolved consistently eventually trips the breaker
                # without needing the verify call to raise.
                strikes = await bump_failure(
                    bucket=VerifierBreakerBucket,
                    workspace_id=str(workspace_id),
                    window_seconds=_BREAKER_WINDOW_SECONDS,
                    recover_seconds=_BREAKER_RECOVER_SECONDS,
                )
                if strikes >= _BREAKER_TRIP_AT:
                    await _audit_safe(
                        workspace_id=workspace_id,
                        action="verifier.breaker_tripped",
                        summary=(
                            f"verifier breaker opened for ws={workspace_id} "
                            f"after {strikes} errored runs"
                        ),
                        metadata={
                            "strikes": int(strikes),
                            "trip_at": _BREAKER_TRIP_AT,
                            "recover_seconds": _BREAKER_RECOVER_SECONDS,
                        },
                    )
                    ws_short_circuit = True
                ws_consecutive_success = 0

        workspaces_swept += 1

    return {
        "workspaces_swept": int(workspaces_swept),
        "workspaces_skipped_breaker": int(workspaces_skipped_breaker),
        "workspaces_skipped_disabled": int(workspaces_skipped_disabled),
        "versions_verified": int(versions_verified),
        "versions_accepted": int(versions_accepted),
        "versions_rejected": int(versions_rejected),
        "versions_skipped_insufficient": int(versions_skipped_insufficient),
        "versions_errored": int(versions_errored),
    }


async def on_skill_verify_job_failed_permanent(ctx: dict[str, Any], exc: BaseException) -> None:
    """ARQ permanent-failure hook for the verifier sweep.

    Mirrors the pending-memory backstop: workspace-level audit is
    skipped (the sweep returns aggregated counts, so a permanent
    failure is the orchestration crashing rather than one tenant
    breaking) and the per-workspace breaker absorbs row-level
    issues. Best-effort.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action="verifier.failed_permanent",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary="verify_proposed_versions_sweep failed permanently",
                metadata={
                    "function": str(ctx.get("function") or ""),
                    "error_class": type(exc).__name__,
                    "job_try": int(ctx.get("job_try", 0) or 0),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("verifier permanent-failure audit write failed")
