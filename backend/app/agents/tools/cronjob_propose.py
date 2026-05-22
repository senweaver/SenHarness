"""Agent self-scheduling propose verb (M2.8).

The platform-builtin evolver agent (M2.2) calls ``propose_cronjob_create``
to ask an admin for a recurring or one-shot Flow (e.g. "every morning
at 09:00 read me the OKR"). Nothing under this module materialises a
:class:`~app.db.models.flow.Flow` row directly — it only writes an
:class:`~app.db.models.approval.Approval` (``resource_type='flow_create'``)
that the M2.5 dispatch handler will eventually translate into a Flow
row (which itself lands ``enabled=False`` so an admin must flip it on
from the Flow UI as the second human gate).

Hard invariants:

* The verb checks the workspace ``evolver.enabled`` flag first; a
  disabled workspace short-circuits with ``code='evolver.disabled'``.
* Cross-workspace ``target_agent_id`` / ``delivery_channel_ids`` are
  rejected with the dedicated audit metadata ``code`` values listed in
  the M2.8 brief (``cross_workspace_agent`` / ``cross_workspace_channel``).
* Schedule strings are parsed into one of three shapes:

      cron        ``"0 9 * * *"`` (5 whitespace-separated fields)
      interval    ``"every 2h"`` / ``"every 30m"`` (regex
                  ``^every (\\d+)([smhd])$``, amount strictly > 0)
      one_shot    ISO 8601 timestamp, must resolve into the future

  Anything else returns ``code='invalid_schedule'``.
* The breaker bucket is shared with the skill propose verbs
  (``EVOLVER_BREAKER_BUCKET = "evolver"``) so a sick evolver pipeline
  trips both surfaces simultaneously. The rate budget is a separate
  bucket (``cronjob_propose``) capped at 5/min to keep cronjob spam
  from burning the workspace's skill-propose allowance.
* Approval TTL is read off
  :class:`app.schemas.platform_settings.EvolverApprovalTtlDays`
  (``flow_create`` field, default 7 days).
* The runner returns a structured payload (``{"status": ...}``) and
  never raises; the agent run continues on rejection so the model
  sees the failure code and can self-correct.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools._context import ToolRunContext, get_context
from app.agents.tools.skill_propose import (
    AUDIT_BREAKER_TRIPPED,
    EVOLVER_BREAKER_BUCKET,
    _check_workspace_enabled,
    _rejected,
)
from app.core.security import utcnow_naive
from app.db.models.agent import Agent
from app.db.models.approval import Approval, ApprovalResourceType, ApprovalStatus
from app.db.models.channel import Channel
from app.db.session import get_session_factory
from app.jobs._breaker import bump_failure, consume_rate, is_breaker_open
from app.repositories.approval import ApprovalRepository
from app.schemas.platform_settings import EvolverSettings
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


__all__ = [
    "AUDIT_PROPOSED",
    "AUDIT_REJECTED",
    "CRONJOB_PROPOSE_RATE_BUCKET",
    "CRONJOB_PROPOSE_RATE_PER_MINUTE",
    "ProposeCronjobArgs",
    "ScheduleKind",
    "ScheduleParseError",
    "parse_schedule",
    "run_propose_cronjob",
]


# ─── Constants ───────────────────────────────────────────────
# Separate from EVOLVER_PROPOSE_RATE_BUCKET — cron proposals have a
# tighter cap (5/min vs the skill propose 10/min default) so the
# runaway-cron failure mode can't burn the workspace's skill-propose
# allowance in the same window.
CRONJOB_PROPOSE_RATE_BUCKET = "cronjob_propose"
CRONJOB_PROPOSE_RATE_PER_MINUTE = 5

# Sentinel used for ``Approval.tool_name`` (NOT NULL since M1.4) on
# non-tool rows; mirrors the leading underscore + verb suffix shape
# established by the skill propose verbs.
_PROPOSAL_TOOL_NAME = "_propose_cronjob_create"

AUDIT_PROPOSED = "evolver.proposed_cronjob"
AUDIT_REJECTED = "evolver.cronjob_rejected"

ScheduleKind = Literal["cron", "interval", "one_shot"]

_INTERVAL_RE = re.compile(r"^every (\d+)([smhd])$")
_INTERVAL_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 3600,
    "d": 86400,
}


# ─── Argument model ──────────────────────────────────────────
class ProposeCronjobArgs(BaseModel):
    """Inputs the agent supplies when proposing a self-scheduled flow.

    ``target_agent_id`` defaults to the calling agent at runtime when
    the model omits it (``None`` here, normalised inside the runner so
    the agent can simply say "schedule this for me"). ``delivery_channel_ids``
    is optional — when omitted the eventual Flow runs in chat mode.
    """

    name: str = Field(min_length=1, max_length=120)
    schedule: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "5-field cron expression (UTC), 'every Nu' interval "
            "(u in {s,m,h,d}), or ISO 8601 one-shot timestamp."
        ),
    )
    prompt_template: str = Field(min_length=1, max_length=4000)
    target_agent_id: uuid.UUID | None = Field(
        default=None,
        description="Defaults to the calling agent if omitted.",
    )
    delivery_channel_ids: list[uuid.UUID] | None = Field(
        default=None,
        max_length=20,
        description="Optional IM channels the eventual Flow will fan out to.",
    )
    rationale: str = Field(min_length=1, max_length=1000)


# ─── Schedule parser ─────────────────────────────────────────
class ScheduleParseError(ValueError):
    """Raised when ``schedule`` does not match any of the three shapes."""


def parse_schedule(schedule: str) -> tuple[ScheduleKind, dict[str, Any]]:
    """Validate ``schedule`` and return ``(kind, parsed_metadata)``.

    Order: interval regex → 5-field cron → ISO 8601 timestamp. The
    interval regex is unambiguous (literal ``"every "`` prefix), the
    cron path goes through APScheduler's
    :meth:`~apscheduler.triggers.cron.CronTrigger.from_crontab` which
    raises on bad expressions, and the one-shot path uses
    :meth:`datetime.fromisoformat` and refuses past timestamps.
    """
    text = schedule.strip()
    if not text:
        raise ScheduleParseError("schedule is empty")

    interval_match = _INTERVAL_RE.match(text)
    if interval_match is not None:
        amount = int(interval_match.group(1))
        unit = interval_match.group(2)
        if amount <= 0:
            raise ScheduleParseError("interval amount must be positive (got 'every 0...')")
        seconds = amount * _INTERVAL_UNIT_SECONDS[unit]
        return "interval", {
            "expression": text,
            "amount": amount,
            "unit": unit,
            "seconds": seconds,
        }

    parts = text.split()
    if len(parts) == 5:
        # APScheduler is the runtime that would actually fire this
        # job; using its parser keeps validation and execution honest
        # against the same grammar.
        try:
            from apscheduler.triggers.cron import CronTrigger
        except ImportError as exc:  # pragma: no cover - dep is required
            raise ScheduleParseError(f"apscheduler not installed: {exc}") from exc
        try:
            CronTrigger.from_crontab(text, timezone="UTC")
        except Exception as exc:
            raise ScheduleParseError(f"invalid cron expression: {exc}") from exc
        return "cron", {"expression": text, "expr": text, "tz": "UTC"}

    try:
        parsed_dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ScheduleParseError(
            "schedule must be a 5-field cron, 'every Nu', or ISO 8601 timestamp"
        ) from exc

    if parsed_dt.tzinfo is not None:
        normalised = parsed_dt.astimezone(UTC).replace(tzinfo=None)
    else:
        normalised = parsed_dt

    if normalised <= utcnow_naive():
        raise ScheduleParseError("one-shot schedule must be in the future")

    return "one_shot", {
        "expression": text,
        "run_at": normalised.isoformat(),
    }


# ─── Internal helpers ────────────────────────────────────────
async def _audit_rejected(
    db: AsyncSession,
    *,
    ctx: ToolRunContext,
    code: str,
    message: str,
    extras: dict[str, Any] | None = None,
) -> None:
    """Stable audit row for every rejection branch.

    The metadata ``code`` is one of the values listed in the M2.8
    brief (``invalid_schedule`` / ``cross_workspace_agent`` /
    ``cross_workspace_channel`` / ``rate_limited`` / ``evolver.disabled``).
    """
    metadata: dict[str, Any] = {
        "resource_type": ApprovalResourceType.FLOW_CREATE.value,
        "code": code,
        "message": message,
    }
    if extras:
        metadata.update(extras)
    await audit_svc.record(
        db,
        action=AUDIT_REJECTED,
        actor_identity_id=ctx.identity_id,
        workspace_id=ctx.workspace_id,
        resource_type="workspace",
        resource_id=ctx.workspace_id,
        summary=f"evolver cronjob proposal rejected ({code})",
        metadata=metadata,
    )


async def _check_breaker_and_rate(
    *,
    workspace_id: uuid.UUID,
    config: EvolverSettings,
    db: AsyncSession,
    ctx: ToolRunContext,
) -> dict[str, Any] | None:
    """Return a rejection payload when the breaker or rate budget says no.

    Shares the breaker bucket with the skill propose verbs
    (``EVOLVER_BREAKER_BUCKET``) — a sick evolver pipeline trips both
    surfaces — but uses an isolated rate bucket capped at
    ``CRONJOB_PROPOSE_RATE_PER_MINUTE`` so cronjob spam does not
    consume the workspace's skill-propose allowance.
    """
    workspace_str = str(workspace_id)
    tripped = await is_breaker_open(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=workspace_str,
        trip_at=int(config.evolver_breaker_strikes),
    )
    if tripped:
        await audit_svc.record(
            db,
            action=AUDIT_BREAKER_TRIPPED,
            actor_identity_id=ctx.identity_id,
            workspace_id=workspace_id,
            resource_type="workspace",
            resource_id=workspace_id,
            summary="evolver cronjob proposal blocked by tripped breaker",
            metadata={
                "bucket": EVOLVER_BREAKER_BUCKET,
                "strikes": int(config.evolver_breaker_strikes),
                "window_seconds": int(config.evolver_breaker_window_seconds),
                "surface": "cronjob",
            },
        )
        return _rejected(
            "evolver.breaker_tripped",
            "Evolver breaker is open; back off and retry after the cooldown.",
        )

    allowed = await consume_rate(
        bucket=CRONJOB_PROPOSE_RATE_BUCKET,
        workspace_id=workspace_str,
        limit=CRONJOB_PROPOSE_RATE_PER_MINUTE,
        period_seconds=60,
    )
    if not allowed:
        return _rejected(
            "evolver.rate_limited",
            (
                f"Workspace burned its cronjob propose budget "
                f"({CRONJOB_PROPOSE_RATE_PER_MINUTE}/min)."
            ),
        )
    return None


def _ttl_for_flow_create(config: EvolverSettings) -> int:
    return int(config.approval_ttl_days.flow_create)


async def _agent_belongs_to_workspace(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> bool:
    stmt = select(Agent.id).where(
        Agent.id == agent_id,
        Agent.workspace_id == workspace_id,
        Agent.deleted_at.is_(None),
    )
    return (await db.execute(stmt)).first() is not None


async def _channels_belong_to_workspace(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    channel_ids: list[uuid.UUID],
) -> tuple[bool, list[uuid.UUID]]:
    """Return ``(all_belong, missing_ids)``."""
    if not channel_ids:
        return True, []
    stmt = select(Channel.id).where(
        Channel.id.in_(channel_ids),
        Channel.workspace_id == workspace_id,
        Channel.deleted_at.is_(None),
    )
    found_rows = (await db.execute(stmt)).scalars().all()
    found = set(found_rows)
    missing = [cid for cid in channel_ids if cid not in found]
    return len(missing) == 0, missing


async def _has_pending_duplicate(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    name: str,
    schedule: str,
) -> uuid.UUID | None:
    """Return the id of an existing pending proposal with the same shape.

    Cheap idempotency guard so the agent can re-issue the same call
    without spamming the admin queue. Two proposals are "the same"
    when they share workspace + name + schedule + still pending.
    """
    stmt = (
        select(Approval.id)
        .where(
            Approval.workspace_id == workspace_id,
            Approval.resource_type == ApprovalResourceType.FLOW_CREATE.value,
            Approval.status == ApprovalStatus.PENDING,
            Approval.tool_args["name"].astext == name,
            Approval.tool_args["schedule"].astext == schedule,
        )
        .limit(1)
    )
    row = (await db.execute(stmt)).first()
    return row[0] if row is not None else None


async def _bump_breaker_on_internal_error(
    *, workspace_id: uuid.UUID, config: EvolverSettings
) -> None:
    """Bump the shared evolver breaker on internal pipeline failure.

    Distinct from "agent proposed something the validator rejected" —
    the breaker exists to stop a misbehaving evolver pipeline (DB
    failure, internal exception) from looping forever.
    """
    await bump_failure(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        window_seconds=int(config.evolver_breaker_window_seconds),
    )


# ─── Verb runner ─────────────────────────────────────────────
async def run_propose_cronjob(args: ProposeCronjobArgs) -> dict:
    """File a ``flow_create`` Approval row; never mutates Flow state."""
    ctx = get_context()
    factory = get_session_factory()
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                code=disabled["code"],
                message=disabled["message"],
                extras={"name": args.name},
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            if gate["code"] == "evolver.rate_limited":
                # The brief lists ``rate_limited`` as one of the
                # canonical metadata codes for evolver.cronjob_rejected;
                # write it before returning so admin dashboards see
                # the surface-specific rejection trail.
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    code="rate_limited",
                    message=gate["message"],
                    extras={"name": args.name},
                )
            await db.commit()
            return gate

        try:
            try:
                schedule_kind, schedule_meta = parse_schedule(args.schedule)
            except ScheduleParseError as exc:
                payload = _rejected(
                    "evolver.invalid_schedule",
                    str(exc),
                    schedule=args.schedule,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    code="invalid_schedule",
                    message=str(exc),
                    extras={"name": args.name, "schedule": args.schedule},
                )
                await db.commit()
                return payload

            target_agent_id = args.target_agent_id or ctx.agent_id
            if not await _agent_belongs_to_workspace(
                db,
                workspace_id=ctx.workspace_id,
                agent_id=target_agent_id,
            ):
                payload = _rejected(
                    "evolver.cross_workspace_agent",
                    (
                        f"target_agent_id {target_agent_id} is not in this "
                        f"workspace; cron flows must reference local agents."
                    ),
                    target_agent_id=str(target_agent_id),
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    code="cross_workspace_agent",
                    message=payload["message"],
                    extras={
                        "name": args.name,
                        "target_agent_id": str(target_agent_id),
                    },
                )
                await db.commit()
                return payload

            delivery_ids = list(args.delivery_channel_ids or [])
            if delivery_ids:
                ok, missing = await _channels_belong_to_workspace(
                    db,
                    workspace_id=ctx.workspace_id,
                    channel_ids=delivery_ids,
                )
                if not ok:
                    payload = _rejected(
                        "evolver.cross_workspace_channel",
                        (
                            f"delivery_channel_ids contained {len(missing)} "
                            f"channel(s) not in this workspace."
                        ),
                        missing_channel_ids=[str(m) for m in missing],
                    )
                    await _audit_rejected(
                        db,
                        ctx=ctx,
                        code="cross_workspace_channel",
                        message=payload["message"],
                        extras={
                            "name": args.name,
                            "missing_channel_ids": [str(m) for m in missing],
                        },
                    )
                    await db.commit()
                    return payload

            existing = await _has_pending_duplicate(
                db,
                workspace_id=ctx.workspace_id,
                name=args.name,
                schedule=args.schedule,
            )
            if existing is not None:
                payload = _rejected(
                    "evolver.duplicate_pending",
                    (
                        f"A pending cronjob proposal named {args.name!r} with "
                        f"the same schedule is already in the queue."
                    ),
                    approval_id=str(existing),
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    code="duplicate_pending",
                    message=payload["message"],
                    extras={"name": args.name, "approval_id": str(existing)},
                )
                await db.commit()
                return payload

            body: dict[str, Any] = {
                "kind": ApprovalResourceType.FLOW_CREATE.value,
                "name": args.name,
                "schedule": args.schedule,
                "schedule_kind": schedule_kind,
                "schedule_meta": schedule_meta,
                "prompt_template": args.prompt_template,
                "target_agent_id": str(target_agent_id),
                "delivery_channel_ids": [str(c) for c in delivery_ids],
                "rationale": args.rationale,
            }
            ttl_days = _ttl_for_flow_create(config)
            expires_at = utcnow_naive() + timedelta(days=ttl_days)
            summary = f"Evolver proposes cronjob {args.name!r} (schedule_kind={schedule_kind})"
            approval = await ApprovalRepository(db).create(
                workspace_id=ctx.workspace_id,
                session_id=None,
                agent_id=ctx.agent_id,
                run_id=ctx.run_id,
                tool_name=_PROPOSAL_TOOL_NAME,
                tool_args=body,
                summary=summary,
                requested_by_identity_id=ctx.identity_id,
                expires_at=expires_at,
                resource_type=ApprovalResourceType.FLOW_CREATE.value,
                resource_id=None,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED,
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="flow_proposal",
                resource_id=approval.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "name": args.name,
                    "schedule": args.schedule,
                    "schedule_kind": schedule_kind,
                    "target_agent_id": str(target_agent_id),
                    "delivery_channel_count": len(delivery_ids),
                    "rationale": args.rationale,
                    "ttl_days": ttl_days,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": ApprovalResourceType.FLOW_CREATE.value,
                "approval_id": str(approval.id),
                "schedule_kind": schedule_kind,
                "expires_at": (
                    approval.expires_at.isoformat() if approval.expires_at is not None else None
                ),
            }
        except Exception:
            log.exception(
                "evolver propose_cronjob_create failed (workspace=%s name=%s)",
                ctx.workspace_id,
                args.name,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(workspace_id=ctx.workspace_id, config=config)
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the cronjob proposal; the breaker counter advanced.",
            )
