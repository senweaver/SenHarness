"""Approval dispatch handler — turn an approved Approval row into actual state (M2.5).

Background
----------

M2.1 + M2.7 + M2.8 file Approval rows for six skill verbs plus the
cron-flow verb. Until M2.5 those rows just sat in the queue with
``status='pending'`` waiting for a human; an admin clicking *approve*
changed ``status`` only — none of the underlying state (SkillPack
version activation, archive transition, file write, Flow row creation)
ever happened automatically. This module is the bridge.

Design rules (locked)
---------------------

1. **Single dispatch entry-point.** The approve endpoint calls
   :func:`dispatch_approved_approval` *before* the commit; if dispatch
   raises :class:`DispatchError` the whole approve path rolls back so
   the row stays pending and the admin sees the error.
2. **No-op on legacy tool-call rows.** Approvals where
   ``resource_type is None`` (the original tool_name path) skip
   dispatch entirely — :func:`dispatch_approved_approval` returns
   ``None`` and the caller proceeds with the standard tool-call
   approval flow.
3. **Each handler owns its own audit row.** The dispatch handler
   writes one ``evolver.applied_<verb>`` (or ``curator.applied_archive``)
   row whose metadata mirrors the M2.7 ``evolver.proposed_<verb>``
   shape. The caller writes the generic ``approval.decide`` row on top.
4. **Breaker reset on success.** Every successful evolver-sourced apply
   resets the workspace's ``evolver:fail:<workspace_id>`` Redis breaker
   so a healthy admin-approved pipeline clears the back-off pressure
   the M2.7 propose verbs may have built up.
5. **Pinned packs win.** Even after admin approval, an ARCHIVE / DELETE
   apply that hits ``PackPinnedAutoSkipped`` returns a result with
   ``audit_action='approval.dispatch_skipped_pinned'`` instead of
   raising — admins commonly approve in batches and a pin between
   propose and approve must not roll the rest of the batch back.

Resource-type → action mapping
------------------------------

================================  ====================================================
``resource_type``                 Action
================================  ====================================================
``skill_pack_create``             ``activate_version(version_id)`` + transition
                                  pack DRAFT→ACTIVE
``skill_pack_patch``              ``activate_version(version_id)`` only (pack is
                                  already ACTIVE)
``skill_pack_edit``               same as patch
``skill_pack_delete``             ``transition(target=ARCHIVED, actor_kind=evolver,
                                  bypass_pinned=False)``
``skill_pack_archive``            ``transition(target=ARCHIVED, actor_kind=curator,
                                  bypass_pinned=False)``  (M1.4 source path)
``skill_pack_write_file``         create / update SkillFile row
``skill_pack_remove_file``        soft-delete SkillFile row
``flow_create``                   create Flow row with ``enabled=False`` (admin
                                  must explicitly enable on the Flow UI as the
                                  second human gate)
``hub_promotion`` (M3.3)          :func:`hub_pull_push.apply_promotion`
                                  — re-runs sanitize + dedup, inserts /
                                  reuses :class:`HubSkillPack` +
                                  :class:`HubSkillPackVersion`,
                                  back-subscribes the source workspace
``subagent_hallucination_review`` (M2.5.1) approve → SubAgentRun → COMPLETED
                                  + ``subagent.hallucination_approved`` audit;
                                  reject (incl. TTL expiry) → SubAgentRun →
                                  KILLED + cancel parent loop +
                                  ``subagent.hallucination_rejected`` audit
``None`` (legacy tool-call)       no-op, return None
================================  ====================================================
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalResourceType
from app.db.models.flow import Flow, FlowExecutionMode, FlowTriggerKind
from app.db.models.skills import SkillPackState
from app.repositories.flow import FlowRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.services import audit as audit_svc
from app.services import skill_lifecycle as lifecycle_svc
from app.services import skill_version as skill_version_svc

log = logging.getLogger(__name__)

__all__ = [
    "AUDIT_DISPATCH_FAILED",
    "AUDIT_DISPATCH_SKIPPED_PINNED",
    "AUDIT_PER_RESOURCE",
    "DispatchError",
    "DispatchResult",
    "dispatch_approved_approval",
]


# ─── Audit action keys (one stable string per resource_type) ──────
AUDIT_PER_RESOURCE: dict[str, str] = {
    ApprovalResourceType.SKILL_PACK_CREATE.value: "evolver.applied_skill_pack_create",
    ApprovalResourceType.SKILL_PACK_PATCH.value: "evolver.applied_skill_pack_patch",
    ApprovalResourceType.SKILL_PACK_EDIT.value: "evolver.applied_skill_pack_edit",
    ApprovalResourceType.SKILL_PACK_DELETE.value: "evolver.applied_skill_pack_delete",
    ApprovalResourceType.SKILL_PACK_WRITE_FILE.value: "evolver.applied_skill_pack_write_file",
    ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value: "evolver.applied_skill_pack_remove_file",
    ApprovalResourceType.SKILL_PACK_ARCHIVE.value: "curator.applied_archive",
    ApprovalResourceType.FLOW_CREATE.value: "evolver.applied_flow_create",
}

AUDIT_DISPATCH_FAILED = "approval.dispatch_failed"
AUDIT_DISPATCH_SKIPPED_PINNED = "approval.dispatch_skipped_pinned"

# Resource types whose source pipeline is the M2.x evolver, not the
# M1.4 curator. Used to decide whether to reset the evolver Redis
# breaker on a successful apply.
_EVOLVER_SOURCED: frozenset[str] = frozenset(
    {
        ApprovalResourceType.SKILL_PACK_CREATE.value,
        ApprovalResourceType.SKILL_PACK_PATCH.value,
        ApprovalResourceType.SKILL_PACK_EDIT.value,
        ApprovalResourceType.SKILL_PACK_DELETE.value,
        ApprovalResourceType.SKILL_PACK_WRITE_FILE.value,
        ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value,
        ApprovalResourceType.FLOW_CREATE.value,
    }
)

# Resource types we recognise but do not dispatch. ``hub_promotion``
# moved out of this set in M3.3 once :func:`hub_pull_push.apply_promotion`
# landed; ``subagent_hallucination_review`` moved out in M2.5.1. The
# set stays as a stable extension point for future M3+ verbs that
# may approve-and-defer.
_NO_DISPATCH_RESOURCE_TYPES: frozenset[str] = frozenset()


# ─── Errors ──────────────────────────────────────────────────
class DispatchError(AppError):
    """Raised when applying an approved approval fails.

    The approve API path catches this, rolls back the transaction
    (so the row stays pending), and re-surfaces the stable ``code``
    in the 409 response. Subclassing :class:`AppError` keeps the
    fastapi error mapper / i18n hooks consistent.
    """

    code = "approval.dispatch_failed"
    default_status = 409


# ─── Result envelope ─────────────────────────────────────────
@dataclass(slots=True)
class DispatchResult:
    """Structured outcome of a successful dispatch.

    The frontend uses ``applied_object_id`` to navigate to the newly
    activated version / flow / archived pack so the admin can verify
    the change immediately after approving.
    """

    approval_id: uuid.UUID
    resource_type: str
    resource_id: uuid.UUID | None
    applied_object_id: uuid.UUID | None
    audit_action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "approval_id": str(self.approval_id),
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id) if self.resource_id else None,
            "applied_object_id": (
                str(self.applied_object_id) if self.applied_object_id else None
            ),
            "audit_action": self.audit_action,
        }


# ─── Public entry point ──────────────────────────────────────
async def dispatch_approved_approval(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult | None:
    """Apply the side effect for an approved Approval row.

    Returns ``None`` for legacy tool-call rows (``resource_type is None``)
    and for the M3-reserved verbs that have no dispatch yet.

    Raises :class:`DispatchError` on internal failure; the caller is
    expected to roll back the transaction so the row stays pending.
    """
    rt = approval.resource_type
    if rt is None:
        return None

    if rt in _NO_DISPATCH_RESOURCE_TYPES:
        log.info(
            "approval.dispatch noop for resource_type=%s approval=%s",
            rt,
            approval.id,
        )
        return None

    handler = _HANDLERS.get(rt)
    if handler is None:
        log.warning(
            "approval.dispatch unknown resource_type=%s approval=%s — noop",
            rt,
            approval.id,
        )
        return None

    try:
        result = await handler(db, approval=approval, actor_identity_id=actor_identity_id)
    except lifecycle_svc.PackPinnedAutoSkipped as exc:
        # The admin approved an archive/delete proposal but the pack got
        # pinned in the meantime. The lifecycle service already wrote
        # ``skill.transition_skipped_pinned`` audit; we add a pinned-
        # skip audit on top so the approval feed shows the bypass.
        # We don't rollback the session here — calling ``rollback()``
        # would also rollback any savepoint the bulk endpoint set up.
        # The lifecycle service raises BEFORE mutating ``pack.state``
        # so no state actually leaked.
        await audit_svc.record(
            db,
            action=AUDIT_DISPATCH_SKIPPED_PINNED,
            actor_identity_id=actor_identity_id,
            workspace_id=approval.workspace_id,
            resource_type="approval",
            resource_id=approval.id,
            summary=(
                f"approval {approval.id} apply skipped: pack {exc.pack_id} pinned"
            ),
            metadata={
                "approval_id": str(approval.id),
                "resource_type": rt,
                "pack_id": str(exc.pack_id),
            },
        )
        return DispatchResult(
            approval_id=approval.id,
            resource_type=rt,
            resource_id=approval.resource_id,
            applied_object_id=None,
            audit_action=AUDIT_DISPATCH_SKIPPED_PINNED,
        )
    except DispatchError:
        # Caller (API or bulk endpoint) is expected to roll back the
        # surrounding transaction / savepoint; the durable
        # ``approval.dispatch_failed`` audit is written on a fresh
        # session by the API layer after the rollback completes.
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception(
            "approval.dispatch failed approval=%s resource_type=%s",
            approval.id,
            rt,
        )
        raise DispatchError(
            f"dispatch failed: {type(exc).__name__}: {exc}",
            code="approval.dispatch_failed",
            extras={
                "approval_id": str(approval.id),
                "resource_type": rt,
                "error_class": type(exc).__name__,
            },
        ) from exc

    # Success path — reset breaker if evolver-sourced. Best-effort,
    # never lets a Redis blip break the apply.
    if rt in _EVOLVER_SOURCED:
        try:
            from app.jobs._breaker import reset_failure  # noqa: PLC0415

            await reset_failure(
                bucket="evolver", workspace_id=str(approval.workspace_id)
            )
        except Exception:  # pragma: no cover - best-effort
            log.warning(
                "approval.dispatch breaker reset failed (workspace=%s)",
                approval.workspace_id,
            )

    return result


# ─── Per-resource_type handlers ──────────────────────────────
async def _apply_skill_pack_version(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``skill_pack_create`` / ``_patch`` / ``_edit``.

    All three carry ``body['version_id']`` pointing at the candidate
    ``SkillPackVersion(state=PROPOSED|ACCEPTED)``. We promote it to
    ACTIVE via :func:`activate_version` (which retires the previous
    ACTIVE row and mirrors content_md back onto the SkillPack), and
    for *create* additionally flip the parent pack from DRAFT to
    ACTIVE so the runtime injection path picks it up.
    """
    rt = cast(str, approval.resource_type)
    body = approval.tool_args or {}
    version_id_raw = body.get("version_id")
    if not version_id_raw:
        raise DispatchError(
            "approval body missing version_id",
            code="approval.dispatch_invalid_body",
            extras={"approval_id": str(approval.id), "resource_type": rt},
        )
    try:
        version_id = uuid.UUID(str(version_id_raw))
    except (TypeError, ValueError) as exc:
        raise DispatchError(
            f"invalid version_id in approval body: {version_id_raw!r}",
            code="approval.dispatch_invalid_body",
        ) from exc

    activated = await skill_version_svc.activate_version(
        db,
        workspace_id=approval.workspace_id,
        version_id=version_id,
        actor_identity_id=actor_identity_id,
        reason=f"approval {approval.id} approved",
    )

    # Create proposals: flip DRAFT → ACTIVE so build_skills_capability
    # picks up the pack. patch / edit operate on already-ACTIVE packs.
    if rt == ApprovalResourceType.SKILL_PACK_CREATE.value:
        pack = await SkillPackRepository(db).get(
            activated.pack_id, include_deleted=True
        )
        if pack is not None and pack.state == SkillPackState.DRAFT:
            await lifecycle_svc.transition(
                db,
                pack_id=pack.id,
                workspace_id=approval.workspace_id,
                target_state=SkillPackState.CANDIDATE,
                actor_identity_id=actor_identity_id,
                reason=f"approval {approval.id}: evolver create approved",
                bypass_pinned=True,
                actor_kind="evolver",
            )
            await lifecycle_svc.transition(
                db,
                pack_id=pack.id,
                workspace_id=approval.workspace_id,
                target_state=SkillPackState.ACTIVE,
                actor_identity_id=actor_identity_id,
                reason=f"approval {approval.id}: evolver create approved",
                bypass_pinned=True,
                actor_kind="evolver",
            )
            pack.enabled = True
            await db.flush([pack])

    audit_action = AUDIT_PER_RESOURCE[rt]
    await audit_svc.record(
        db,
        action=audit_action,
        actor_identity_id=actor_identity_id,
        workspace_id=approval.workspace_id,
        resource_type="skill_pack_version",
        resource_id=activated.id,
        summary=(
            f"applied {rt} → activated v{activated.version_no} for pack {activated.pack_id}"
        ),
        metadata={
            "approval_id": str(approval.id),
            "pack_id": str(activated.pack_id),
            "version_id": str(activated.id),
            "version_no": int(activated.version_no),
            "content_hash": activated.content_hash,
            "resource_type": rt,
        },
    )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=rt,
        resource_id=approval.resource_id,
        applied_object_id=activated.id,
        audit_action=audit_action,
    )


async def _apply_skill_pack_archive(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``skill_pack_delete`` (evolver) or ``_archive`` (curator).

    Both transition the pack to ``ARCHIVED`` via the lifecycle service.
    The actor_kind reflects the source so the audit trail still shows
    whether the human-curated curator pipeline or the agentic evolver
    pipeline filed the proposal.
    """
    rt = cast(str, approval.resource_type)
    if approval.resource_id is None:
        raise DispatchError(
            "archive approval missing resource_id (pack_id)",
            code="approval.dispatch_invalid_body",
        )

    actor_kind: lifecycle_svc.ActorKind = (
        "curator"
        if rt == ApprovalResourceType.SKILL_PACK_ARCHIVE.value
        else "evolver"
    )
    pack = await SkillPackRepository(db).get(
        approval.resource_id, include_deleted=True
    )
    if pack is None or pack.workspace_id != approval.workspace_id:
        raise DispatchError(
            f"pack {approval.resource_id} not found",
            code="approval.dispatch_pack_missing",
        )
    if pack.state in (SkillPackState.ARCHIVED, SkillPackState.TOMBSTONE):
        # Idempotent — re-running approve on an already-archived pack
        # is a no-op, audit it and report the existing pack id back.
        audit_action = AUDIT_PER_RESOURCE[rt]
        await audit_svc.record(
            db,
            action=audit_action,
            actor_identity_id=actor_identity_id,
            workspace_id=approval.workspace_id,
            resource_type="skill_pack",
            resource_id=pack.id,
            summary=f"approval {approval.id}: pack already in {pack.state.value}, noop",
            metadata={
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "current_state": pack.state.value,
                "resource_type": rt,
                "noop": True,
            },
        )
        return DispatchResult(
            approval_id=approval.id,
            resource_type=rt,
            resource_id=approval.resource_id,
            applied_object_id=pack.id,
            audit_action=audit_action,
        )

    await lifecycle_svc.transition(
        db,
        pack_id=pack.id,
        workspace_id=approval.workspace_id,
        target_state=SkillPackState.ARCHIVED,
        actor_identity_id=actor_identity_id,
        reason=f"approval {approval.id}: {rt} approved",
        bypass_pinned=False,
        actor_kind=actor_kind,
    )

    audit_action = AUDIT_PER_RESOURCE[rt]
    await audit_svc.record(
        db,
        action=audit_action,
        actor_identity_id=actor_identity_id,
        workspace_id=approval.workspace_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=f"applied {rt} → archived pack {pack.slug!r}",
        metadata={
            "approval_id": str(approval.id),
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "actor_kind": actor_kind,
            "resource_type": rt,
        },
    )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=rt,
        resource_id=approval.resource_id,
        applied_object_id=pack.id,
        audit_action=audit_action,
    )


async def _apply_skill_pack_write_file(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``skill_pack_write_file``.

    Creates or updates the SkillFile row. Idempotent — repeating the
    apply on the same body does not create a second row, just updates
    the existing one.
    """
    rt = ApprovalResourceType.SKILL_PACK_WRITE_FILE.value
    body = approval.tool_args or {}
    pack_id_raw = body.get("pack_id") or approval.resource_id
    relative_path = body.get("relative_path")
    content = body.get("content")
    if not pack_id_raw or not relative_path or content is None:
        raise DispatchError(
            "write_file approval missing pack_id/relative_path/content",
            code="approval.dispatch_invalid_body",
        )
    try:
        pack_id = uuid.UUID(str(pack_id_raw))
    except (TypeError, ValueError) as exc:
        raise DispatchError(
            f"invalid pack_id in approval body: {pack_id_raw!r}",
            code="approval.dispatch_invalid_body",
        ) from exc

    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != approval.workspace_id:
        raise DispatchError(
            f"pack {pack_id} not found", code="approval.dispatch_pack_missing"
        )
    if pack.state == SkillPackState.TOMBSTONE:
        raise DispatchError(
            "cannot write file on tombstoned pack",
            code="approval.dispatch_pack_tombstoned",
        )

    file_repo = SkillFileRepository(db)
    files = await file_repo.list_for_pack(
        workspace_id=approval.workspace_id, skill_pack_id=pack.id
    )
    existing = next((f for f in files if f.path == relative_path), None)
    if existing is None:
        target = await file_repo.create(
            workspace_id=approval.workspace_id,
            skill_pack_id=pack.id,
            path=relative_path,
            content_md=str(content),
        )
    else:
        existing.content_md = str(content)
        await db.flush([existing])
        target = existing

    audit_action = AUDIT_PER_RESOURCE[rt]
    await audit_svc.record(
        db,
        action=audit_action,
        actor_identity_id=actor_identity_id,
        workspace_id=approval.workspace_id,
        resource_type="skill_file",
        resource_id=target.id,
        summary=(
            f"applied {rt} → wrote {relative_path!r} on pack {pack.slug!r}"
        ),
        metadata={
            "approval_id": str(approval.id),
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "relative_path": relative_path,
            "file_id": str(target.id),
            "created": existing is None,
        },
    )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=rt,
        resource_id=approval.resource_id,
        applied_object_id=target.id,
        audit_action=audit_action,
    )


async def _apply_skill_pack_remove_file(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``skill_pack_remove_file``.

    Soft-deletes the SkillFile row via the SoftDeleteMixin. Returns a
    no-op-style result if the row was already gone.
    """
    rt = ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value
    body = approval.tool_args or {}
    pack_id_raw = body.get("pack_id") or approval.resource_id
    relative_path = body.get("relative_path")
    if not pack_id_raw or not relative_path:
        raise DispatchError(
            "remove_file approval missing pack_id/relative_path",
            code="approval.dispatch_invalid_body",
        )
    try:
        pack_id = uuid.UUID(str(pack_id_raw))
    except (TypeError, ValueError) as exc:
        raise DispatchError(
            f"invalid pack_id in approval body: {pack_id_raw!r}",
            code="approval.dispatch_invalid_body",
        ) from exc

    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != approval.workspace_id:
        raise DispatchError(
            f"pack {pack_id} not found",
            code="approval.dispatch_pack_missing",
        )

    file_repo = SkillFileRepository(db)
    files = await file_repo.list_for_pack(
        workspace_id=approval.workspace_id, skill_pack_id=pack.id
    )
    target = next((f for f in files if f.path == relative_path), None)
    file_id: uuid.UUID | None = None
    if target is not None:
        target.deleted_at = utcnow_naive()
        await db.flush([target])
        file_id = target.id

    audit_action = AUDIT_PER_RESOURCE[rt]
    await audit_svc.record(
        db,
        action=audit_action,
        actor_identity_id=actor_identity_id,
        workspace_id=approval.workspace_id,
        resource_type="skill_pack",
        resource_id=pack.id,
        summary=(
            f"applied {rt} → removed {relative_path!r} from pack {pack.slug!r}"
        ),
        metadata={
            "approval_id": str(approval.id),
            "pack_id": str(pack.id),
            "slug": pack.slug,
            "relative_path": relative_path,
            "file_id": str(file_id) if file_id is not None else None,
            "noop": target is None,
        },
    )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=rt,
        resource_id=approval.resource_id,
        applied_object_id=file_id,
        audit_action=audit_action,
    )


async def _apply_flow_create(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``flow_create``.

    Creates a new ``Flow(enabled=False)`` row from the cronjob propose
    body. The disabled-by-default invariant is the second human gate
    promised in the M2.8 brief: even though the admin approved the
    proposal, an explicit toggle on the Flow UI is still required
    before any cron tick fires.
    """
    rt = ApprovalResourceType.FLOW_CREATE.value
    body = approval.tool_args or {}
    name = body.get("name")
    schedule_kind = body.get("schedule_kind")
    schedule_meta = body.get("schedule_meta") or {}
    prompt_template = body.get("prompt_template") or ""
    target_agent_raw = body.get("target_agent_id")
    delivery_channel_ids = body.get("delivery_channel_ids") or []

    if not name or not schedule_kind:
        raise DispatchError(
            "flow_create approval body missing name/schedule_kind",
            code="approval.dispatch_invalid_body",
        )

    try:
        target_agent_id = (
            uuid.UUID(str(target_agent_raw)) if target_agent_raw else None
        )
    except (TypeError, ValueError) as exc:
        raise DispatchError(
            f"invalid target_agent_id: {target_agent_raw!r}",
            code="approval.dispatch_invalid_body",
        ) from exc

    trigger_kind, trigger_config = _flow_trigger_from_schedule(
        schedule_kind=schedule_kind, schedule_meta=schedule_meta
    )

    flow_repo = FlowRepository(db)
    flow: Flow = await flow_repo.create(
        workspace_id=approval.workspace_id,
        name=str(name)[:128],
        description=(body.get("rationale") or "")[:512] or None,
        trigger_kind=trigger_kind,
        trigger_config=trigger_config,
        execution_mode=FlowExecutionMode.AGENT,
        agent_id=target_agent_id,
        squad_id=None,
        prompt_template=prompt_template,
        graph_json={},
        # Second human gate — admin must flip enabled=True on the Flow
        # UI before the scheduler picks it up.
        enabled=False,
        metadata_json={
            "delivery_channel_ids": [str(c) for c in delivery_channel_ids],
            "origin": {
                "approval_id": str(approval.id),
                "proposed_by": "evolver",
            },
            "schedule_kind": schedule_kind,
        },
        created_by=actor_identity_id,
    )
    await db.flush([flow])

    audit_action = AUDIT_PER_RESOURCE[rt]
    await audit_svc.record(
        db,
        action=audit_action,
        actor_identity_id=actor_identity_id,
        workspace_id=approval.workspace_id,
        resource_type="flow",
        resource_id=flow.id,
        summary=(
            f"applied {rt} → created Flow {name!r} (enabled=False, await admin toggle)"
        ),
        metadata={
            "approval_id": str(approval.id),
            "flow_id": str(flow.id),
            "name": str(name),
            "schedule_kind": schedule_kind,
            "trigger_kind": trigger_kind.value,
            "target_agent_id": str(target_agent_id) if target_agent_id else None,
            "delivery_channel_count": len(delivery_channel_ids),
        },
    )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=rt,
        resource_id=approval.resource_id,
        applied_object_id=flow.id,
        audit_action=audit_action,
    )


# ─── Schedule → Flow trigger translator ──────────────────────
_INTERVAL_TO_CRON = {
    "s": None,  # second-precision intervals don't fit a 5-field cron
    "m": "*/{} * * * *",
    "h": "0 */{} * * *",
    "d": "0 0 */{} * *",
}
_VALID_INTERVAL_AMOUNTS_FOR_CRON = re.compile(r"^[1-9]\d?$")


def _flow_trigger_from_schedule(
    *, schedule_kind: str, schedule_meta: dict[str, Any]
) -> tuple[FlowTriggerKind, dict[str, Any]]:
    """Translate cronjob_propose schedule into a Flow trigger config.

    cron → ``CRON`` with the same expression. interval (``every Nu``)
    → ``CRON`` with a translated 5-field expression when feasible
    (every 30m → ``*/30 * * * *``); seconds + non-trivial day intervals
    fall back to a metadata-tagged CRON entry that the scheduler may
    refuse to fire — admin sees it in the Flow UI and can fix it
    manually.
    """
    if schedule_kind == "cron":
        expr = schedule_meta.get("expr") or schedule_meta.get("expression")
        if not expr:
            raise DispatchError(
                "cron schedule meta missing expr",
                code="approval.dispatch_invalid_body",
            )
        return FlowTriggerKind.CRON, {
            "expr": str(expr),
            "tz": str(schedule_meta.get("tz", "UTC")),
        }

    if schedule_kind == "interval":
        unit = schedule_meta.get("unit")
        amount = schedule_meta.get("amount")
        cron_template = _INTERVAL_TO_CRON.get(str(unit))
        if (
            cron_template is not None
            and isinstance(amount, int)
            and _VALID_INTERVAL_AMOUNTS_FOR_CRON.match(str(amount))
        ):
            return FlowTriggerKind.CRON, {
                "expr": cron_template.format(amount),
                "tz": "UTC",
                "interval_origin": schedule_meta.get("expression"),
            }
        # Unsupported precision — keep the original spec, mark as
        # requiring admin intervention before enable.
        return FlowTriggerKind.CRON, {
            "expr": "* * * * *",
            "tz": "UTC",
            "interval_origin": schedule_meta.get("expression"),
            "needs_manual_review": True,
        }

    if schedule_kind == "one_shot":
        run_at = schedule_meta.get("run_at")
        return FlowTriggerKind.MANUAL, {
            "one_shot_at": run_at,
            "origin": "evolver_propose_cronjob",
        }

    raise DispatchError(
        f"unknown schedule_kind: {schedule_kind!r}",
        code="approval.dispatch_invalid_body",
    )


async def _apply_subagent_hallucination_review(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``subagent_hallucination_review`` (M2.5.1).

    Admin approve → SubAgentRun → COMPLETED, the parent's wait
    resolves on the next heartbeat tick. The aux-LLM-judged final
    output is treated as authoritative.

    Admin reject → SubAgentRun → KILLED, the parent should observe
    ``state=KILLED`` and cancel the child. The TTL processor reuses
    this same handler indirectly: when a hallucination_review row
    expires the TTL pass calls ``reject_approval`` and the row never
    reaches dispatch — but operators may still call this handler
    directly via the explicit reject path, so the audit shape stays
    consistent.

    Idempotent: a missing spine row (race against the reaper) returns
    a DispatchResult with ``applied_object_id=None`` and the
    ``approval.dispatch_skipped_pinned`` audit shape — admin sees
    "approved but child already gone" rather than a 409.
    """
    rt = ApprovalResourceType_subagent_hallucination_review
    from app.services import subagent_run as subagent_svc  # noqa: PLC0415

    updated = await subagent_svc.apply_hallucination_decision(
        db,
        approval=approval,
        approved=True,  # _HANDLERS only fires for approve path
        actor_identity_id=actor_identity_id,
    )
    if updated is None:
        # Spine row gone — log a no-op so the admin sees the row
        # as approved but knows nothing actually changed.
        await audit_svc.record(
            db,
            action="subagent.hallucination_approved",
            actor_identity_id=actor_identity_id,
            workspace_id=approval.workspace_id,
            resource_type="approval",
            resource_id=approval.id,
            summary=(
                f"approval {approval.id} hallucination_review approved "
                f"but spine row already terminal — noop"
            ),
            metadata={
                "approval_id": str(approval.id),
                "resource_type": rt,
                "noop": True,
            },
        )
        return DispatchResult(
            approval_id=approval.id,
            resource_type=rt,
            resource_id=approval.resource_id,
            applied_object_id=None,
            audit_action="subagent.hallucination_approved",
        )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=rt,
        resource_id=approval.resource_id,
        applied_object_id=updated.id,
        audit_action="subagent.hallucination_approved",
    )


# Local alias to avoid a circular import: the resource_type string
# lives in :mod:`app.services.subagent_run` as the canonical constant
# but referencing the enum here keeps the dispatch table spelling
# uniform. Kept as a module-level binding so reading the dispatch
# table stays a one-line scan.
ApprovalResourceType_subagent_hallucination_review = (
    "subagent_hallucination_review"
)


async def _apply_hub_promotion(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_identity_id: uuid.UUID | None,
) -> DispatchResult:
    """Dispatch for ``hub_promotion`` (M3.3).

    Delegates to :func:`hub_pull_push.apply_promotion` which re-runs
    the M3.2 preview, inserts (or reuses) the hub pack + version,
    and back-subscribes the source workspace. The handler writes a
    single ``hub.promotion_applied`` audit row carrying the dedup
    flag + new hub version metadata.
    """
    from app.services import hub_pull_push as hub_pp_svc  # noqa: PLC0415

    result = await hub_pp_svc.apply_promotion(
        db,
        approval_id=approval.id,
        actor_identity_id=actor_identity_id,
    )
    return DispatchResult(
        approval_id=approval.id,
        resource_type=hub_pp_svc.HUB_PROMOTION_RESOURCE_TYPE,
        resource_id=approval.resource_id,
        applied_object_id=cast("uuid.UUID", result["hub_pack_id"]),
        audit_action=hub_pp_svc.AUDIT_PROMOTION_APPLIED,
    )


# ─── Handler dispatch table ──────────────────────────────────
_HANDLERS = {
    ApprovalResourceType.SKILL_PACK_CREATE.value: _apply_skill_pack_version,
    ApprovalResourceType.SKILL_PACK_PATCH.value: _apply_skill_pack_version,
    ApprovalResourceType.SKILL_PACK_EDIT.value: _apply_skill_pack_version,
    ApprovalResourceType.SKILL_PACK_DELETE.value: _apply_skill_pack_archive,
    ApprovalResourceType.SKILL_PACK_ARCHIVE.value: _apply_skill_pack_archive,
    ApprovalResourceType.SKILL_PACK_WRITE_FILE.value: _apply_skill_pack_write_file,
    ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value: _apply_skill_pack_remove_file,
    ApprovalResourceType.FLOW_CREATE.value: _apply_flow_create,
    "subagent_hallucination_review": _apply_subagent_hallucination_review,
    "hub_promotion": _apply_hub_promotion,
}
