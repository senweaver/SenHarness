"""Hub auto-pull ARQ sweep (M3.3).

Schedule
--------

Runs every 30 minutes on minute ``{6, 36}``. The slot is wedged into
the dead zone between the existing crons:

* M0.11 retention sweep — minute = ``{0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}``
* M0.7 pending memory sweep — minute = ``{2, 32}``
* M0.3 judge periodic sweep — minute = ``15``
* M2.4 verifier sweep — minute = ``{7, 37}``
* M2.5 approval-TTL sweep — minute = ``22``

Minutes ``{6, 36}`` are the only 30-minute cadence that doesn't
touch any neighbour. The 5-minute retention buckets at ``{5, 35}``
scope to disjoint tables (identities / workspaces) so they don't
contend for connection-pool slots.

Job behaviour (per workspace × subscription)
--------------------------------------------

For each non-deleted workspace:

  For each :class:`WorkspaceHubSubscription` where ``auto_pull=True``:

    1. Skip when the subscription's ``last_pulled_version_no`` already
       matches the hub pack's currently active version (the
       :func:`pull_now` no-op path also writes this — the cheap
       cursor check here just avoids re-entering the service).
    2. Otherwise call :func:`hub_pull_push.pull_now`. The drafted
       local SkillPackVersion stays in ``state=PROPOSED`` so the
       M2.4 verifier still has to clear it before the workspace's
       runtime injection picks it up — auto-pull does not bypass
       approval.

Failure isolation
-----------------

A per-subscription exception is caught + logged + counted; the
sweep continues to the next row. After three consecutive failures
on the same subscription we audit ``hub.auto_pull_failed_permanent``
and skip that subscription for the remainder of the tick (a future
operator UI may surface the badge). Only an outer crash
(e.g. ``get_session_factory`` itself raises) bubbles to ARQ so the
worker's ``max_tries=3`` retry budget protects against transient
infra blips.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.models.workspace import Workspace
from app.db.models.workspace_hub_subscription import WorkspaceHubSubscription
from app.db.session import get_session_factory
from app.repositories.hub_skill_pack import HubSkillPackVersionRepository
from app.services import audit as audit_svc
from app.services import hub_pull_push as hub_pp_svc
from app.services import hub_skill as hub_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_AUTO_PULL_FAILED_PERMANENT",
    "AUDIT_AUTO_PULL_SWEEP_FAILED_PERMANENT",
    "HUB_AUTO_PULL_SWEEP_NAME",
    "hub_auto_pull_sweep",
    "on_hub_auto_pull_job_failed_permanent",
]


HUB_AUTO_PULL_SWEEP_NAME = "hub_auto_pull_sweep"
AUDIT_AUTO_PULL_FAILED_PERMANENT = hub_pp_svc.AUDIT_AUTO_PULL_FAILED_PERMANENT
AUDIT_AUTO_PULL_SWEEP_FAILED_PERMANENT = "hub.auto_pull_sweep_failed_permanent"

# Per-subscription retry budget inside one tick. We don't carry the
# counter across ticks — three failures inside one tick is already
# the strong signal we want to audit.
_PER_SUBSCRIPTION_RETRY_LIMIT = 3


# ── Cron entrypoint ─────────────────────────────────────────
async def hub_auto_pull_sweep(ctx: dict[str, Any]) -> dict[str, Any]:
    """30-minute hub auto-pull sweep across every non-deleted workspace.

    Returns a JSON-serialisable summary mirroring the curator / evolver
    sweep shape so operator dashboards can compare ticks side by side.
    """
    _ = ctx
    factory = get_session_factory()
    summary: dict[str, Any] = {
        "status": "ok",
        "workspaces_seen": 0,
        "workspaces_swept": 0,
        "subscriptions_seen": 0,
        "subscriptions_pulled": 0,
        "subscriptions_skipped_up_to_date": 0,
        "subscriptions_skipped_no_active": 0,
        "subscriptions_skipped_disabled_hub": 0,
        "subscriptions_failed": 0,
        "subscriptions_failed_permanent": 0,
    }

    async with factory() as db:
        try:
            await hub_svc.require_hub_enabled(db)
        except Exception as exc:  # noqa: BLE001
            # Hub disabled at the platform level → no-op sweep, but
            # still emit a summary so the operator UI can show the
            # tick happened.
            log.info("hub_auto_pull skipped: hub disabled (%s)", exc)
            summary["status"] = "hub_disabled"
            return summary

        ws_rows = (
            (
                await db.execute(
                    select(Workspace.id).where(
                        Workspace.deleted_at.is_(None)
                    )
                )
            )
            .scalars()
            .all()
        )

    for ws_id in ws_rows:
        summary["workspaces_seen"] += 1
        per_ws = await _sweep_workspace(workspace_id=ws_id)
        summary["subscriptions_seen"] += per_ws["seen"]
        summary["subscriptions_pulled"] += per_ws["pulled"]
        summary["subscriptions_skipped_up_to_date"] += per_ws["up_to_date"]
        summary["subscriptions_skipped_no_active"] += per_ws["no_active"]
        summary["subscriptions_failed"] += per_ws["failed"]
        summary["subscriptions_failed_permanent"] += per_ws["failed_permanent"]
        if per_ws["seen"] > 0:
            summary["workspaces_swept"] += 1

    return summary


async def _sweep_workspace(*, workspace_id: uuid.UUID) -> dict[str, int]:
    counts = {
        "seen": 0,
        "pulled": 0,
        "up_to_date": 0,
        "no_active": 0,
        "failed": 0,
        "failed_permanent": 0,
    }
    factory = get_session_factory()

    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(WorkspaceHubSubscription).where(
                        WorkspaceHubSubscription.workspace_id == workspace_id,
                        WorkspaceHubSubscription.auto_pull.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
        # Pre-fetch active version_no per hub pack so we can short-
        # circuit before opening a fresh session inside ``pull_now``.
        active_cursor: dict[uuid.UUID, int | None] = {}
        version_repo = HubSkillPackVersionRepository(db)
        for sub in rows:
            if sub.hub_pack_id in active_cursor:
                continue
            active = await version_repo.get_active(hub_pack_id=sub.hub_pack_id)
            active_cursor[sub.hub_pack_id] = (
                active.version_no if active is not None else None
            )

    counts["seen"] = len(rows)
    if not rows:
        return counts

    for sub in rows:
        active_no = active_cursor.get(sub.hub_pack_id)
        if active_no is None:
            counts["no_active"] += 1
            continue
        if (
            sub.last_pulled_version_no is not None
            and sub.last_pulled_version_no >= active_no
        ):
            counts["up_to_date"] += 1
            continue

        outcome = await _pull_one_with_retry(
            workspace_id=workspace_id,
            hub_pack_id=sub.hub_pack_id,
            subscription_id=sub.id,
        )
        if outcome == "pulled":
            counts["pulled"] += 1
        elif outcome == "up_to_date":
            counts["up_to_date"] += 1
        elif outcome == "no_active":
            counts["no_active"] += 1
        elif outcome == "failed_permanent":
            counts["failed"] += 1
            counts["failed_permanent"] += 1
        else:
            counts["failed"] += 1

    return counts


async def _pull_one_with_retry(
    *,
    workspace_id: uuid.UUID,
    hub_pack_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> str:
    """Run :func:`pull_now` with up to three attempts.

    Returns the pull status string (``pulled`` / ``up_to_date`` /
    ``no_active_version``) on success, or ``failed_permanent`` after
    three attempts. Each attempt opens a fresh session so a poisoned
    transaction can't bleed into the next try.
    """
    factory = get_session_factory()
    last_exc: BaseException | None = None
    for attempt in range(_PER_SUBSCRIPTION_RETRY_LIMIT):
        try:
            async with factory() as db:
                result = await hub_pp_svc.pull_now(
                    db,
                    workspace_id=workspace_id,
                    hub_pack_id=hub_pack_id,
                    actor_identity_id=None,
                )
                await db.commit()
                return result.status
        except hub_pp_svc.HubSubscriptionNotFound:
            # Subscription vanished between the fetch and the pull —
            # nothing to retry. Treat as a soft skip.
            return "no_active"
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning(
                "hub_auto_pull attempt %d failed for ws=%s pack=%s: %s",
                attempt + 1,
                workspace_id,
                hub_pack_id,
                exc,
            )

    # Three strikes — audit + skip past so the head-of-line
    # subscription doesn't block the rest of the workspace's sweep.
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=AUDIT_AUTO_PULL_FAILED_PERMANENT,
                actor_identity_id=None,
                workspace_id=workspace_id,
                resource_type="hub_subscription",
                resource_id=subscription_id,
                summary=(
                    f"hub auto-pull failed permanently after "
                    f"{_PER_SUBSCRIPTION_RETRY_LIMIT} attempts "
                    f"(ws={workspace_id} pack={hub_pack_id})"
                ),
                metadata={
                    "task": HUB_AUTO_PULL_SWEEP_NAME,
                    "workspace_id": str(workspace_id),
                    "hub_pack_id": str(hub_pack_id),
                    "subscription_id": str(subscription_id),
                    "exception": repr(last_exc),
                    "attempts": _PER_SUBSCRIPTION_RETRY_LIMIT,
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit must not raise
        log.exception(
            "hub_auto_pull permanent-failure audit write failed ws=%s pack=%s",
            workspace_id,
            hub_pack_id,
        )
    return "failed_permanent"


# ── ARQ permanent-failure hook ──────────────────────────────
async def on_hub_auto_pull_job_failed_permanent(
    ctx: dict[str, Any], exc: BaseException
) -> None:
    """Three-strike hook for the ARQ frame around the sweep.

    Sister to the curator / evolver / verifier hooks: writes one
    stable audit row so operators can spot the dead-letter sweep
    without trawling Redis. Best-effort; never re-raises.
    """
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=AUDIT_AUTO_PULL_SWEEP_FAILED_PERMANENT,
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=(
                    f"{HUB_AUTO_PULL_SWEEP_NAME} failed permanently: {exc!r}"
                ),
                metadata={
                    "function": str(
                        ctx.get("function") or HUB_AUTO_PULL_SWEEP_NAME
                    ),
                    "job_id": ctx.get("job_id"),
                    "exception": repr(exc),
                    "job_try": ctx.get("job_try"),
                    "max_tries": ctx.get("max_tries"),
                },
            )
            await db.commit()
    except Exception:  # pragma: no cover
        log.exception("on_hub_auto_pull_job_failed_permanent hook crashed")
