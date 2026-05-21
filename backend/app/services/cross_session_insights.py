"""Cross-session insights service (M4.5).

User-facing entry point for the ``/insights [--days N]`` slash command
plus the matching REST surface in ``app.api.v1.insights``. The service
is intentionally thin — every long-running step lives behind the ARQ
``generate_insights`` task in :mod:`app.jobs.insights`. The methods
here only:

* parse the slash command shape;
* validate the day window against the workspace ``InsightsSettings``;
* gate the request on the shared evolver breaker (per the M4.5 design
  point — sick aux trips one bucket; degrading silently would be
  dishonest);
* enqueue the ARQ task + write one ``insights.queued`` audit row.

Per design decision 3 the breaker bucket is shared with the evolver
agent (``EVOLVER_BREAKER_BUCKET = "evolver"``) so a single sick aux
LLM disables every cross-session signal at once. Per design decision 4
the privacy boundary is ``(workspace_id, identity_id)`` — neither the
service nor the ARQ task ever surfaces another user's artifacts.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools.skill_propose import EVOLVER_BREAKER_BUCKET
from app.core.errors import AppError, ValidationFailed
from app.jobs._breaker import is_breaker_open
from app.schemas.platform_settings import EvolverSettings, InsightsSettings
from app.services import audit as audit_svc
from app.services.evolver_config import get_workspace_evolver_config
from app.services.system_settings import SystemSettingKey, get_system_setting

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_AUX_SKIPPED",
    "AUDIT_FAILED_PERMANENT",
    "AUDIT_QUEUED",
    "AUDIT_SUMMARIZED",
    "INSIGHTS_WORKSPACE_KEY",
    "InsightsBreakerOpen",
    "InsightsDisabled",
    "get_workspace_insights_config",
    "parse_insights_command",
    "queue_insights_generation",
]


AUDIT_QUEUED = "insights.queued"
AUDIT_SUMMARIZED = "insights.cross_session_summarized"
AUDIT_AUX_SKIPPED = "insights.aux_skipped"
AUDIT_FAILED_PERMANENT = "insights.failed_permanent"

INSIGHTS_WORKSPACE_KEY = "insights"


# ─── Errors ──────────────────────────────────────────────────
class InsightsDisabled(AppError):
    code = "insights.disabled"
    default_status = 409


class InsightsBreakerOpen(AppError):
    code = "insights.breaker_open"
    default_status = 503


# ─── Slash command parser ────────────────────────────────────
# Recognised shapes (case-insensitive command, leading/trailing space ok):
#   "/insights"               → status (default day window)
#   "/insights --days N"      → custom day window (positive integer)
#   "/insights --days=N"      → equivalent (= or space)
#
# Anything else returns ``None`` so the caller can fall through to the
# regular agent loop without disturbing the existing ``/goal`` parser
# (which already returns ``None`` for ``/insights …`` payloads — see
# the unit test in ``test_goal_slash_command``).
_INSIGHTS_BARE_RE = re.compile(r"^\s*/insights\s*$", re.IGNORECASE)
_INSIGHTS_DAYS_RE = re.compile(
    r"^\s*/insights\s+--days(?:\s+|=)(?P<days>-?\d+)\s*$", re.IGNORECASE
)


async def parse_insights_command(text: str) -> dict[str, Any] | None:
    """Return ``{"days": int | None}`` or ``None`` if not /insights.

    ``days=None`` means the caller asked for the default window —
    :func:`queue_insights_generation` substitutes the workspace
    ``default_days`` later. A literal ``--days N`` always wins over
    the default. Negative / zero / non-numeric values are returned
    verbatim so the validation layer in
    :func:`queue_insights_generation` can produce a structured
    rejection (rather than the parser silently dropping the user's
    intent).
    """
    if not text or not text.lstrip().lower().startswith("/insights"):
        return None
    if _INSIGHTS_BARE_RE.match(text):
        return {"days": None}
    m = _INSIGHTS_DAYS_RE.match(text)
    if m:
        try:
            return {"days": int(m.group("days"))}
        except ValueError:
            return {"days": None}
    # Recognised the prefix but the body is malformed — surface the
    # same shape so the caller treats it as an /insights attempt and
    # writes one audit row, rather than letting the LLM see the raw
    # ``/insights ???`` text.
    return {"days": None}


# ─── Workspace config resolver ───────────────────────────────
async def get_workspace_insights_config(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> InsightsSettings:
    """Merge workspace ``home_config_json["insights"]`` over platform default.

    Mirrors :func:`app.services.evolver_config.get_workspace_evolver_config`:
    workspace overrides win on every field; missing fields back-fill
    from the platform default. The workspace ``home_config_json``
    already lives on the ``Workspace`` row so we side-load it via the
    repository to keep the read self-contained.
    """
    raw = await get_system_setting(
        db, SystemSettingKey.INSIGHTS_DEFAULTS, default=None
    )
    if not isinstance(raw, dict):
        raw = InsightsSettings().model_dump(mode="json")
    merged: dict[str, Any] = dict(raw)

    from app.repositories.workspace import WorkspaceRepository

    ws = await WorkspaceRepository(db).get(workspace_id)
    if ws is not None and isinstance(ws.home_config_json, dict):
        override = ws.home_config_json.get(INSIGHTS_WORKSPACE_KEY)
        if isinstance(override, dict):
            for key, value in override.items():
                if value is None:
                    continue
                merged[key] = value

    return InsightsSettings.model_validate(merged)


# ─── Queue dispatcher ────────────────────────────────────────
async def queue_insights_generation(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    return_session_id: uuid.UUID,
    days: int | None = None,
    actor_identity_id: uuid.UUID | None = None,
    invocation_kind: str = "slash_command",
) -> dict[str, Any]:
    """Validate + breaker-check + enqueue ``generate_insights``.

    Returns ``{"queued": True, "days": <int>, "expected_completion_seconds": 30}``
    on success. Raises :class:`ValidationFailed` for an out-of-range
    ``days`` value, :class:`InsightsDisabled` when the workspace toggle
    is off, and :class:`InsightsBreakerOpen` when the shared evolver
    breaker is open.

    ``actor_identity_id`` defaults to ``identity_id`` — the identity
    whose history is being summarised is also the actor on the audit
    row by convention. Service callers (REST endpoint) may override it
    when the call originates from a workspace admin running on behalf
    of another user (M4.5 ships only the same-identity path; the
    parameter is wired so M5+ admin tooling lands without a service
    rewrite).
    """
    config = await get_workspace_insights_config(
        db, workspace_id=workspace_id
    )
    if not config.enabled:
        raise InsightsDisabled(
            "cross-session insights are disabled for this workspace",
        )

    resolved_days = int(config.default_days) if days is None else int(days)
    if resolved_days < 1 or resolved_days > int(config.max_days):
        raise ValidationFailed(
            "days_out_of_range",
            code="insights.days_out_of_range",
            extras={
                "min": 1,
                "max": int(config.max_days),
                "got": resolved_days,
            },
        )

    evolver_cfg = await get_workspace_evolver_config(
        db, workspace_id=workspace_id
    )
    breaker_open = await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        trip_at=int(_breaker_trip_at(evolver_cfg)),
    )
    if breaker_open:
        # Audit must survive even though we're about to raise — open a
        # fresh DB session so the caller's transaction can roll back
        # without losing the breaker-skipped breadcrumb. Mirrors the
        # pattern in ``services/workspace_quota.py`` for failed-create
        # audits.
        await _audit_breaker_skipped_in_fresh_session(
            actor_identity_id=actor_identity_id or identity_id,
            workspace_id=workspace_id,
            return_session_id=return_session_id,
            resolved_days=resolved_days,
            identity_id=identity_id,
            invocation_kind=invocation_kind,
        )
        raise InsightsBreakerOpen(
            "shared evolver breaker is open; aux signals temporarily disabled",
        )

    job_id = await _enqueue_generate(
        workspace_id=workspace_id,
        identity_id=identity_id,
        return_session_id=return_session_id,
        days=resolved_days,
    )

    await audit_svc.record(
        db,
        action=AUDIT_QUEUED,
        actor_identity_id=actor_identity_id or identity_id,
        workspace_id=workspace_id,
        resource_type="session",
        resource_id=return_session_id,
        summary=f"queued insights generation for last {resolved_days} day(s)",
        metadata={
            "trigger": invocation_kind,
            "days": resolved_days,
            "identity_id": str(identity_id),
            "job_id": job_id,
        },
    )

    return {
        "queued": True,
        "days": resolved_days,
        "expected_completion_seconds": 30,
        "job_id": job_id,
    }


def _breaker_trip_at(evolver_cfg: EvolverSettings) -> int:
    """Resolve the strike count that trips the shared aux breaker.

    Pulled out as a helper so the test harness can swap the strike
    count without monkey-patching the evolver schema. Floors at 1 so a
    misconfigured workspace cannot turn the breaker check into a
    no-op.
    """
    return max(1, int(evolver_cfg.evolver_breaker_strikes or 5))


async def _audit_breaker_skipped_in_fresh_session(
    *,
    actor_identity_id: uuid.UUID,
    workspace_id: uuid.UUID,
    return_session_id: uuid.UUID,
    resolved_days: int,
    identity_id: uuid.UUID,
    invocation_kind: str,
) -> None:
    """Persist ``insights.aux_skipped`` outside the caller's transaction.

    The outer queue dispatcher raises immediately after this call, which
    would otherwise roll back any audit row written on the caller's
    session. Opening a fresh session keeps the breadcrumb durable so an
    operator can still see "user tried /insights at 14:32 but breaker
    was open". Best-effort — never re-raises.
    """
    from app.db.session import get_session_factory

    factory = get_session_factory()
    try:
        async with factory() as fresh_db:
            await audit_svc.record(
                fresh_db,
                action=AUDIT_AUX_SKIPPED,
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="session",
                resource_id=return_session_id,
                summary="insights aux skipped — shared evolver breaker open",
                metadata={
                    "trigger": invocation_kind,
                    "bucket": EVOLVER_BREAKER_BUCKET,
                    "days": resolved_days,
                    "identity_id": str(identity_id),
                },
            )
            await fresh_db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception(
            "insights breaker-skipped audit (fresh session) failed for ws=%s",
            workspace_id,
        )


async def _enqueue_generate(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    return_session_id: uuid.UUID,
    days: int,
) -> str | None:
    """Enqueue the ARQ task; never raises into the service caller."""
    try:
        from app.worker.queue import enqueue

        return await enqueue(
            "generate_insights",
            workspace_id=str(workspace_id),
            identity_id=str(identity_id),
            return_session_id=str(return_session_id),
            days=int(days),
            _workspace_id=workspace_id,
            _identity_id=identity_id,
        )
    except Exception:  # pragma: no cover
        log.exception(
            "insights enqueue failed for workspace=%s identity=%s",
            workspace_id,
            identity_id,
        )
        return None
