"""Six skill-proposal verbs the M2.2 evolver agent calls (M2.1 + M2.7).

Every verb files an :class:`~app.db.models.approval.Approval` row and
either inserts a candidate :class:`~app.db.models.skill_pack_version.SkillPackVersion`
(``create`` / ``patch`` / ``edit``) or attaches the request body to the
approval (``delete`` / ``write_file`` / ``remove_file``). Nothing here
flips ``SkillPack.state`` or activates a version directly — that is
M2.5's dispatch handler, gated on admin approval.

Hard invariants the runner relies on:

* All verbs return a structured payload (``{"status": ...}``) and never
  raise; the agent run continues on rejection so the model sees the
  failure code and can retry / give up gracefully.
* Every verb checks the workspace ``evolver.enabled`` flag first; a
  disabled workspace short-circuits with ``code='evolver.disabled'``.
* The Redis breaker (key ``evolver:fail:<workspace_id>``) is checked
  before the rate budget; a tripped breaker rejects with
  ``code='evolver.breaker_tripped'`` and writes a single audit row so
  ops sees the breaker firing in the activity feed.
* Approvals are created with ``tool_name='_skill_propose_<verb>'``
  (sentinel — column is NOT NULL, see M1.4) and
  ``resource_type=skill_pack_<verb>`` so the M2.5 admin UI routes the
  card to the right renderer.
* Rate budget bucket is ``evolver_propose:<workspace_id>``,
  default 10/min; the workspace-level config can raise it via
  ``evolver_rate_per_minute``.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tools._context import ToolRunContext, get_context
from app.core.security import utcnow_naive
from app.db.models.approval import (
    Approval,
    ApprovalResourceType,
    ApprovalStatus,
)
from app.db.models.skill_pack_version import SkillPackVersionState
from app.db.models.skills import (
    SkillPack,
    SkillPackSource,
    SkillPackState,
)
from app.db.session import get_session_factory
from app.jobs._breaker import bump_failure, consume_rate, is_breaker_open
from app.repositories.approval import ApprovalRepository
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.schemas.platform_settings import EvolverSettings
from app.services import audit as audit_svc
from app.services.evolver_config import get_workspace_evolver_config
from app.services.skill_lifecycle import is_slug_tombstoned
from app.services.skill_version import (
    SkillPackVersionConflict,
    compute_content_hash,
    create_version,
)

log = logging.getLogger(__name__)


__all__ = [
    "EVOLVER_BREAKER_BUCKET",
    "EVOLVER_PROPOSE_RATE_BUCKET",
    "PROPOSAL_TOOL_NAME",
    "ProposeSkillCreateArgs",
    "ProposeSkillDeleteArgs",
    "ProposeSkillEditArgs",
    "ProposeSkillPatchArgs",
    "ProposeSkillRemoveFileArgs",
    "ProposeSkillWriteFileArgs",
    "run_propose_skill_create",
    "run_propose_skill_delete",
    "run_propose_skill_edit",
    "run_propose_skill_patch",
    "run_propose_skill_remove_file",
    "run_propose_skill_write_file",
]


# ─── Constants ───────────────────────────────────────────────
EVOLVER_BREAKER_BUCKET = "evolver"
EVOLVER_PROPOSE_RATE_BUCKET = "evolver_propose"

# Sentinel for ``Approval.tool_name`` on non-tool rows. M1.4 established
# the ``"_skill_pack_archive"`` precedent; we keep the leading
# underscore + verb suffix shape so the admin UI can group + filter
# without parsing the resource_type.
PROPOSAL_TOOL_NAME: dict[str, str] = {
    ApprovalResourceType.SKILL_PACK_CREATE.value: "_skill_propose_create",
    ApprovalResourceType.SKILL_PACK_PATCH.value: "_skill_propose_patch",
    ApprovalResourceType.SKILL_PACK_EDIT.value: "_skill_propose_edit",
    ApprovalResourceType.SKILL_PACK_DELETE.value: "_skill_propose_delete",
    ApprovalResourceType.SKILL_PACK_WRITE_FILE.value: "_skill_propose_write_file",
    ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value: "_skill_propose_remove_file",
}

# Audit action keys (one string each; agreed in the M2.7 brief).
AUDIT_PROPOSED: dict[str, str] = {
    ApprovalResourceType.SKILL_PACK_CREATE.value: "evolver.proposed_skill_create",
    ApprovalResourceType.SKILL_PACK_PATCH.value: "evolver.proposed_skill_patch",
    ApprovalResourceType.SKILL_PACK_EDIT.value: "evolver.proposed_skill_edit",
    ApprovalResourceType.SKILL_PACK_DELETE.value: "evolver.proposed_skill_delete",
    ApprovalResourceType.SKILL_PACK_WRITE_FILE.value: "evolver.proposed_skill_write_file",
    ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value: "evolver.proposed_skill_remove_file",
}
AUDIT_REJECTED = "evolver.propose_rejected"
AUDIT_BREAKER_TRIPPED = "evolver.breaker_tripped"


def _ttl_field_for(resource_type: str) -> str:
    return resource_type  # field names match resource type values 1:1


def _file_excerpt_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _short_excerpt(text: str, *, head: int = 80) -> str:
    """Stable short string for audit/approval bodies (never the full text)."""
    flat = (text or "").replace("\n", " ").strip()
    if len(flat) <= head:
        return flat
    return flat[:head] + "…"


# ─── Argument models ─────────────────────────────────────────
class _BaseProposalArgs(BaseModel):
    """Shared rationale field every verb requires.

    The admin UI prints this verbatim above the diff so the model's
    self-justification is part of the approval record.
    """

    rationale: str = Field(
        min_length=1,
        max_length=1000,
        description="Why this change is being proposed (shown to the admin).",
    )


class ProposeSkillCreateArgs(_BaseProposalArgs):
    slug: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[a-z0-9-]+$",
        description="Slug for the new pack (lowercase, hyphens; never reused once tombstoned).",
    )
    name: str | None = Field(
        default=None,
        max_length=128,
        description="Optional display name; falls back to a humanised slug.",
    )
    description: str | None = Field(default=None, max_length=512)
    content_md: str = Field(min_length=1, max_length=200_000)
    files: dict[str, str] | None = Field(
        default=None,
        description=(
            "Optional ``{path: sha256}`` map of supplementary files; "
            "actual file bytes ride later write_file proposals."
        ),
    )
    supporting_run_ids: list[str] = Field(default_factory=list, max_length=20)


class ProposeSkillPatchArgs(_BaseProposalArgs):
    pack_id: uuid.UUID
    old_text: str = Field(min_length=1, max_length=50_000)
    new_text: str = Field(max_length=50_000)
    supporting_run_ids: list[str] = Field(default_factory=list, max_length=20)


class ProposeSkillEditArgs(_BaseProposalArgs):
    pack_id: uuid.UUID
    new_content_md: str = Field(min_length=1, max_length=200_000)
    supporting_run_ids: list[str] = Field(default_factory=list, max_length=20)


class ProposeSkillDeleteArgs(_BaseProposalArgs):
    pack_id: uuid.UUID


class ProposeSkillWriteFileArgs(_BaseProposalArgs):
    pack_id: uuid.UUID
    relative_path: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9/_.-]+$",
        description="Path relative to the pack folder (e.g. 'scripts/run.sh').",
    )
    content: str = Field(max_length=200_000)


class ProposeSkillRemoveFileArgs(_BaseProposalArgs):
    pack_id: uuid.UUID
    relative_path: str = Field(min_length=1, max_length=200)


# ─── Common gate helpers ─────────────────────────────────────
def _rejected(code: str, message: str, **extras: Any) -> dict[str, Any]:
    return {
        "status": "rejected",
        "code": code,
        "message": message,
        **extras,
    }


async def _check_workspace_enabled(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> tuple[EvolverSettings, dict[str, Any] | None]:
    """Return ``(config, rejected_payload | None)``.

    Centralises the disabled-workspace short-circuit so every verb
    handles it the same way.
    """
    config = await get_workspace_evolver_config(db, workspace_id=workspace_id)
    if not config.enabled:
        return config, _rejected(
            "evolver.disabled",
            "Workspace has the evolver disabled; admin must opt in via /admin/settings/evolver.",
        )
    return config, None


async def _check_breaker_and_rate(
    *,
    workspace_id: uuid.UUID,
    config: EvolverSettings,
    db: AsyncSession,
    ctx: ToolRunContext,
) -> dict[str, Any] | None:
    """Return a rejection payload when the breaker or rate budget says no."""
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
            summary="evolver propose blocked by tripped breaker",
            metadata={
                "bucket": EVOLVER_BREAKER_BUCKET,
                "strikes": int(config.evolver_breaker_strikes),
                "window_seconds": int(config.evolver_breaker_window_seconds),
            },
        )
        return _rejected(
            "evolver.breaker_tripped",
            "Evolver breaker is open; back off and retry after the cooldown.",
        )

    allowed = await consume_rate(
        bucket=EVOLVER_PROPOSE_RATE_BUCKET,
        workspace_id=workspace_str,
        limit=int(config.evolver_rate_per_minute),
        period_seconds=60,
    )
    if not allowed:
        return _rejected(
            "evolver.rate_limited",
            f"Workspace burned its evolver propose budget ({config.evolver_rate_per_minute}/min).",
        )
    return None


async def _audit_rejected(
    db: AsyncSession,
    *,
    ctx: ToolRunContext,
    resource_type: str,
    code: str,
    message: str,
    extras: dict[str, Any] | None = None,
) -> None:
    metadata: dict[str, Any] = {
        "resource_type": resource_type,
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
        summary=f"evolver propose rejected ({resource_type}/{code})",
        metadata=metadata,
    )


def _ttl_for(config: EvolverSettings, resource_type: str) -> int:
    return int(getattr(config.approval_ttl_days, _ttl_field_for(resource_type)))


async def _create_proposal_approval(
    db: AsyncSession,
    *,
    ctx: ToolRunContext,
    config: EvolverSettings,
    resource_type: str,
    resource_id: uuid.UUID,
    body: dict[str, Any],
    summary: str,
) -> Approval:
    expires_at = utcnow_naive() + timedelta(
        days=_ttl_for(config, resource_type)
    )
    repo = ApprovalRepository(db)
    return await repo.create(
        workspace_id=ctx.workspace_id,
        session_id=None,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        tool_name=PROPOSAL_TOOL_NAME[resource_type],
        tool_args=body,
        summary=summary,
        requested_by_identity_id=ctx.identity_id,
        expires_at=expires_at,
        resource_type=resource_type,
        resource_id=resource_id,
    )


async def _bump_breaker_on_internal_error(
    *, workspace_id: uuid.UUID, config: EvolverSettings
) -> None:
    """Bump the breaker counter when the proposal pipeline itself fails.

    Distinct from "agent proposed something the validator rejected" —
    the breaker exists to stop a misbehaving evolver pipeline (DB
    failure, internal exception) from looping forever. Caller wraps
    every verb body in a ``try`` and calls this on the ``except`` path.
    """
    await bump_failure(
        bucket=EVOLVER_BREAKER_BUCKET,
        workspace_id=str(workspace_id),
        window_seconds=int(config.evolver_breaker_window_seconds),
    )


async def _load_pack(
    db: AsyncSession, *, workspace_id: uuid.UUID, pack_id: uuid.UUID
) -> SkillPack | None:
    pack = await SkillPackRepository(db).get(pack_id, include_deleted=True)
    if pack is None or pack.workspace_id != workspace_id:
        return None
    return pack


def _humanise_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


# ─── Verb 1 — propose_skill_create ───────────────────────────
async def run_propose_skill_create(args: ProposeSkillCreateArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()
    resource_type = ApprovalResourceType.SKILL_PACK_CREATE.value
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                resource_type=resource_type,
                code=disabled["code"],
                message=disabled["message"],
                extras={"slug": args.slug},
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            await db.commit()
            return gate

        try:
            if await is_slug_tombstoned(
                db, workspace_id=ctx.workspace_id, slug=args.slug
            ):
                payload = _rejected(
                    "evolver.slug_tombstoned",
                    f"Slug {args.slug!r} was previously tombstoned and cannot be reused.",
                    slug=args.slug,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"slug": args.slug},
                )
                await db.commit()
                return payload

            existing = await SkillPackRepository(db).get_by_slug(
                workspace_id=ctx.workspace_id, slug=args.slug
            )
            if existing is not None:
                payload = _rejected(
                    "evolver.slug_in_use",
                    (
                        f"Slug {args.slug!r} is already taken by an existing pack; "
                        f"propose a patch / edit instead."
                    ),
                    slug=args.slug,
                    pack_id=str(existing.id),
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"slug": args.slug},
                )
                await db.commit()
                return payload

            pack_repo = SkillPackRepository(db)
            pack = await pack_repo.create(
                workspace_id=ctx.workspace_id,
                slug=args.slug,
                name=args.name or _humanise_slug(args.slug),
                description=args.description,
                version="0.1.0",
                publisher=None,
                signature=None,
                source=SkillPackSource.WORKSPACE,
                manifest_json={},
                enabled=False,  # candidate; M2.5 flips to enabled on activate
                metadata_json={"created_by_evolver": True},
                created_by=ctx.identity_id,
                state=SkillPackState.DRAFT,
            )
            await db.flush([pack])

            files_dict = dict(args.files or {})
            try:
                version = await create_version(
                    db,
                    workspace_id=ctx.workspace_id,
                    pack_id=pack.id,
                    content_md=args.content_md,
                    files=files_dict,
                    created_by="evolver",
                    creator_identity_id=ctx.identity_id,
                    source_run_ids=list(args.supporting_run_ids),
                )
            except SkillPackVersionConflict as exc:
                payload = _rejected(
                    "evolver.duplicate_content_hash",
                    "An identical version already exists for this pack.",
                    extras_=exc.extras,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            body: dict[str, Any] = {
                "kind": resource_type,
                "slug": pack.slug,
                "pack_id": str(pack.id),
                "version_id": str(version.id),
                "version_no": int(version.version_no),
                "content_hash": version.content_hash,
                "content_excerpt": _short_excerpt(args.content_md),
                "files": list(files_dict.keys()),
                "supporting_run_ids": list(args.supporting_run_ids),
                "rationale": args.rationale,
            }
            summary = (
                f"Evolver proposes new skill pack {pack.slug!r} "
                f"(v{version.version_no})"
            )
            approval = await _create_proposal_approval(
                db,
                ctx=ctx,
                config=config,
                resource_type=resource_type,
                resource_id=pack.id,
                body=body,
                summary=summary,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED[resource_type],
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="skill_pack",
                resource_id=pack.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "version_id": str(version.id),
                    "version_no": int(version.version_no),
                    "content_hash": version.content_hash,
                    "supporting_run_ids": list(args.supporting_run_ids),
                    "rationale": args.rationale,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": resource_type,
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "version_id": str(version.id),
                "version_no": int(version.version_no),
                "content_hash": version.content_hash,
                "expires_at": (
                    approval.expires_at.isoformat()
                    if approval.expires_at is not None
                    else None
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception(
                "evolver propose_skill_create failed (workspace=%s slug=%s)",
                ctx.workspace_id,
                args.slug,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(
                workspace_id=ctx.workspace_id, config=config
            )
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the create proposal; the breaker counter advanced.",
            )


# ─── Verb 2 — propose_skill_patch ────────────────────────────
async def run_propose_skill_patch(args: ProposeSkillPatchArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()
    resource_type = ApprovalResourceType.SKILL_PACK_PATCH.value
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                resource_type=resource_type,
                code=disabled["code"],
                message=disabled["message"],
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            await db.commit()
            return gate

        try:
            pack = await _load_pack(
                db, workspace_id=ctx.workspace_id, pack_id=args.pack_id
            )
            if pack is None:
                payload = _rejected(
                    "evolver.pack_not_found",
                    f"Skill pack {args.pack_id} does not exist in this workspace.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if pack.state == SkillPackState.TOMBSTONE:
                payload = _rejected(
                    "evolver.pack_tombstoned",
                    "Cannot patch a tombstoned pack.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            version_repo = SkillPackVersionRepository(db)
            current = await version_repo.get_active(
                workspace_id=ctx.workspace_id, pack_id=pack.id
            )
            if current is not None:
                current_text = current.content_md or ""
            else:
                # Pre-M1.2 packs persist their body on the SkillFile row
                # for SKILL.md instead of a version snapshot. Fall back
                # to that so the patch verb works on legacy packs too.
                file_repo = SkillFileRepository(db)
                files = await file_repo.list_for_pack(
                    workspace_id=ctx.workspace_id, skill_pack_id=pack.id
                )
                skill_md = next((f for f in files if f.path == "SKILL.md"), None)
                current_text = skill_md.content_md if skill_md is not None else ""

            if not current_text:
                payload = _rejected(
                    "evolver.no_active_content",
                    "Pack has no active content yet; propose a create or edit instead.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            if args.old_text not in current_text:
                payload = _rejected(
                    "evolver.patch_conflict",
                    (
                        "old_text not found verbatim in the current ACTIVE version; "
                        "re-read the pack content and re-propose."
                    ),
                    pack_id=str(pack.id),
                    current_excerpt=_short_excerpt(current_text, head=160),
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"pack_id": str(pack.id)},
                )
                await db.commit()
                return payload

            new_content = current_text.replace(args.old_text, args.new_text, 1)
            old_excerpt_hash = _file_excerpt_hash(args.old_text)
            new_excerpt_hash = _file_excerpt_hash(args.new_text)

            try:
                version = await create_version(
                    db,
                    workspace_id=ctx.workspace_id,
                    pack_id=pack.id,
                    content_md=new_content,
                    files=current.files_json if current is not None else None,
                    created_by="evolver",
                    creator_identity_id=ctx.identity_id,
                    source_run_ids=list(args.supporting_run_ids),
                )
            except SkillPackVersionConflict as exc:
                payload = _rejected(
                    "evolver.duplicate_content_hash",
                    "Patched content matches an existing version (no-op).",
                    pack_id=str(pack.id),
                )
                payload["existing"] = exc.extras or {}
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"pack_id": str(pack.id)},
                )
                await db.commit()
                return payload

            body: dict[str, Any] = {
                "kind": resource_type,
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "version_id": str(version.id),
                "version_no": int(version.version_no),
                "content_hash": version.content_hash,
                "old_excerpt_hash": old_excerpt_hash,
                "new_excerpt_hash": new_excerpt_hash,
                "old_excerpt": _short_excerpt(args.old_text),
                "new_excerpt": _short_excerpt(args.new_text),
                "supporting_run_ids": list(args.supporting_run_ids),
                "rationale": args.rationale,
            }
            summary = (
                f"Evolver proposes patch on skill pack {pack.slug!r} "
                f"(→ v{version.version_no})"
            )
            approval = await _create_proposal_approval(
                db,
                ctx=ctx,
                config=config,
                resource_type=resource_type,
                resource_id=pack.id,
                body=body,
                summary=summary,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED[resource_type],
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="skill_pack",
                resource_id=pack.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "version_id": str(version.id),
                    "version_no": int(version.version_no),
                    "content_hash": version.content_hash,
                    "old_excerpt_hash": old_excerpt_hash,
                    "new_excerpt_hash": new_excerpt_hash,
                    "supporting_run_ids": list(args.supporting_run_ids),
                    "rationale": args.rationale,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": resource_type,
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "version_id": str(version.id),
                "version_no": int(version.version_no),
                "content_hash": version.content_hash,
                "expires_at": (
                    approval.expires_at.isoformat()
                    if approval.expires_at is not None
                    else None
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception(
                "evolver propose_skill_patch failed (workspace=%s pack=%s)",
                ctx.workspace_id,
                args.pack_id,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(
                workspace_id=ctx.workspace_id, config=config
            )
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the patch proposal; the breaker counter advanced.",
            )


# ─── Verb 3 — propose_skill_edit (full-document replace) ─────
async def run_propose_skill_edit(args: ProposeSkillEditArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()
    resource_type = ApprovalResourceType.SKILL_PACK_EDIT.value
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                resource_type=resource_type,
                code=disabled["code"],
                message=disabled["message"],
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            await db.commit()
            return gate

        try:
            pack = await _load_pack(
                db, workspace_id=ctx.workspace_id, pack_id=args.pack_id
            )
            if pack is None:
                payload = _rejected(
                    "evolver.pack_not_found",
                    f"Skill pack {args.pack_id} does not exist in this workspace.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if pack.state == SkillPackState.TOMBSTONE:
                payload = _rejected(
                    "evolver.pack_tombstoned",
                    "Cannot edit a tombstoned pack.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            version_repo = SkillPackVersionRepository(db)
            current = await version_repo.get_active(
                workspace_id=ctx.workspace_id, pack_id=pack.id
            )
            try:
                version = await create_version(
                    db,
                    workspace_id=ctx.workspace_id,
                    pack_id=pack.id,
                    content_md=args.new_content_md,
                    files=current.files_json if current is not None else None,
                    created_by="evolver",
                    creator_identity_id=ctx.identity_id,
                    source_run_ids=list(args.supporting_run_ids),
                )
            except SkillPackVersionConflict as exc:
                payload = _rejected(
                    "evolver.duplicate_content_hash",
                    "Edited content is identical to an existing version.",
                    pack_id=str(pack.id),
                )
                payload["existing"] = exc.extras or {}
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"pack_id": str(pack.id)},
                )
                await db.commit()
                return payload

            body: dict[str, Any] = {
                "kind": resource_type,
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "version_id": str(version.id),
                "version_no": int(version.version_no),
                "content_hash": version.content_hash,
                "previous_content_hash": (
                    current.content_hash if current is not None else None
                ),
                "content_excerpt": _short_excerpt(args.new_content_md, head=160),
                "supporting_run_ids": list(args.supporting_run_ids),
                "rationale": args.rationale,
            }
            summary = (
                f"Evolver proposes full-document edit on skill pack {pack.slug!r} "
                f"(→ v{version.version_no})"
            )
            approval = await _create_proposal_approval(
                db,
                ctx=ctx,
                config=config,
                resource_type=resource_type,
                resource_id=pack.id,
                body=body,
                summary=summary,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED[resource_type],
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="skill_pack",
                resource_id=pack.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "version_id": str(version.id),
                    "version_no": int(version.version_no),
                    "content_hash": version.content_hash,
                    "supporting_run_ids": list(args.supporting_run_ids),
                    "rationale": args.rationale,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": resource_type,
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "version_id": str(version.id),
                "version_no": int(version.version_no),
                "expires_at": (
                    approval.expires_at.isoformat()
                    if approval.expires_at is not None
                    else None
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception(
                "evolver propose_skill_edit failed (workspace=%s pack=%s)",
                ctx.workspace_id,
                args.pack_id,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(
                workspace_id=ctx.workspace_id, config=config
            )
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the edit proposal; the breaker counter advanced.",
            )


# ─── Verb 4 — propose_skill_delete ───────────────────────────
async def _has_pending_approval_for(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    resource_type: str,
) -> bool:
    stmt = (
        select(Approval.id)
        .where(
            Approval.workspace_id == workspace_id,
            Approval.resource_type == resource_type,
            Approval.resource_id == pack_id,
            Approval.status == ApprovalStatus.PENDING,
        )
        .limit(1)
    )
    return (await db.execute(stmt)).first() is not None


async def run_propose_skill_delete(args: ProposeSkillDeleteArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()
    resource_type = ApprovalResourceType.SKILL_PACK_DELETE.value
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                resource_type=resource_type,
                code=disabled["code"],
                message=disabled["message"],
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            await db.commit()
            return gate

        try:
            pack = await _load_pack(
                db, workspace_id=ctx.workspace_id, pack_id=args.pack_id
            )
            if pack is None:
                payload = _rejected(
                    "evolver.pack_not_found",
                    f"Skill pack {args.pack_id} does not exist in this workspace.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if pack.state == SkillPackState.TOMBSTONE:
                payload = _rejected(
                    "evolver.pack_tombstoned",
                    "Pack is already tombstoned; nothing to delete.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if pack.pinned:
                # Pinned packs are explicit user investment. Auto delete
                # via evolver is rejected outright; the user must unpin
                # first if they want the evolver to consider deletion.
                payload = _rejected(
                    "evolver.pack_pinned",
                    "Pack is pinned; unpin before the evolver can propose deletion.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"pack_id": str(pack.id)},
                )
                await db.commit()
                return payload

            if await _has_pending_approval_for(
                db,
                workspace_id=ctx.workspace_id,
                pack_id=pack.id,
                resource_type=resource_type,
            ):
                payload = _rejected(
                    "evolver.duplicate_pending",
                    "A delete proposal for this pack is already pending review.",
                    pack_id=str(pack.id),
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                    extras={"pack_id": str(pack.id)},
                )
                await db.commit()
                return payload

            body: dict[str, Any] = {
                "kind": resource_type,
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "current_state": pack.state.value,
                "rationale": args.rationale,
            }
            summary = f"Evolver proposes deletion of skill pack {pack.slug!r}"
            approval = await _create_proposal_approval(
                db,
                ctx=ctx,
                config=config,
                resource_type=resource_type,
                resource_id=pack.id,
                body=body,
                summary=summary,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED[resource_type],
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="skill_pack",
                resource_id=pack.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "current_state": pack.state.value,
                    "rationale": args.rationale,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": resource_type,
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "expires_at": (
                    approval.expires_at.isoformat()
                    if approval.expires_at is not None
                    else None
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception(
                "evolver propose_skill_delete failed (workspace=%s pack=%s)",
                ctx.workspace_id,
                args.pack_id,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(
                workspace_id=ctx.workspace_id, config=config
            )
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the delete proposal; the breaker counter advanced.",
            )


# ─── Verb 5 — propose_skill_write_file ───────────────────────
def _is_write_file_path_safe(path: str) -> bool:
    if "//" in path or path.startswith("/") or path.endswith("/"):
        return False
    parts = path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return True


async def run_propose_skill_write_file(args: ProposeSkillWriteFileArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()
    resource_type = ApprovalResourceType.SKILL_PACK_WRITE_FILE.value
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                resource_type=resource_type,
                code=disabled["code"],
                message=disabled["message"],
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            await db.commit()
            return gate

        try:
            if not _is_write_file_path_safe(args.relative_path):
                payload = _rejected(
                    "evolver.invalid_path",
                    "Relative path must not contain '..', '//', or leading/trailing '/'.",
                    relative_path=args.relative_path,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            pack = await _load_pack(
                db, workspace_id=ctx.workspace_id, pack_id=args.pack_id
            )
            if pack is None:
                payload = _rejected(
                    "evolver.pack_not_found",
                    f"Skill pack {args.pack_id} does not exist in this workspace.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if pack.state == SkillPackState.TOMBSTONE:
                payload = _rejected(
                    "evolver.pack_tombstoned",
                    "Cannot add files to a tombstoned pack.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if args.relative_path == "SKILL.md":
                payload = _rejected(
                    "evolver.reserved_path",
                    "SKILL.md is the pack body; use propose_skill_edit / propose_skill_patch.",
                    relative_path=args.relative_path,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            content_hash = _file_excerpt_hash(args.content)
            body: dict[str, Any] = {
                "kind": resource_type,
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "relative_path": args.relative_path,
                "content_hash": content_hash,
                "content_excerpt": _short_excerpt(args.content, head=160),
                "content": args.content,
                "rationale": args.rationale,
            }
            summary = (
                f"Evolver proposes write_file {args.relative_path!r} "
                f"on skill pack {pack.slug!r}"
            )
            approval = await _create_proposal_approval(
                db,
                ctx=ctx,
                config=config,
                resource_type=resource_type,
                resource_id=pack.id,
                body=body,
                summary=summary,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED[resource_type],
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="skill_pack",
                resource_id=pack.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "relative_path": args.relative_path,
                    "content_hash": content_hash,
                    "rationale": args.rationale,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": resource_type,
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "relative_path": args.relative_path,
                "content_hash": content_hash,
                "expires_at": (
                    approval.expires_at.isoformat()
                    if approval.expires_at is not None
                    else None
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception(
                "evolver propose_skill_write_file failed (workspace=%s pack=%s path=%s)",
                ctx.workspace_id,
                args.pack_id,
                args.relative_path,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(
                workspace_id=ctx.workspace_id, config=config
            )
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the write_file proposal; the breaker counter advanced.",
            )


# ─── Verb 6 — propose_skill_remove_file ──────────────────────
async def run_propose_skill_remove_file(args: ProposeSkillRemoveFileArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()
    resource_type = ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value
    async with factory() as db:
        config, disabled = await _check_workspace_enabled(db, workspace_id=ctx.workspace_id)
        if disabled is not None:
            await _audit_rejected(
                db,
                ctx=ctx,
                resource_type=resource_type,
                code=disabled["code"],
                message=disabled["message"],
            )
            await db.commit()
            return disabled

        gate = await _check_breaker_and_rate(
            workspace_id=ctx.workspace_id, config=config, db=db, ctx=ctx
        )
        if gate is not None:
            await db.commit()
            return gate

        try:
            pack = await _load_pack(
                db, workspace_id=ctx.workspace_id, pack_id=args.pack_id
            )
            if pack is None:
                payload = _rejected(
                    "evolver.pack_not_found",
                    f"Skill pack {args.pack_id} does not exist in this workspace.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if pack.state == SkillPackState.TOMBSTONE:
                payload = _rejected(
                    "evolver.pack_tombstoned",
                    "Cannot remove files from a tombstoned pack.",
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload
            if args.relative_path == "SKILL.md":
                payload = _rejected(
                    "evolver.reserved_path",
                    "SKILL.md cannot be removed; propose deletion of the pack instead.",
                    relative_path=args.relative_path,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            file_repo = SkillFileRepository(db)
            files = await file_repo.list_for_pack(
                workspace_id=ctx.workspace_id, skill_pack_id=pack.id
            )
            target = next((f for f in files if f.path == args.relative_path), None)
            if target is None:
                payload = _rejected(
                    "evolver.file_not_found",
                    f"Pack {pack.slug!r} has no file {args.relative_path!r}.",
                    relative_path=args.relative_path,
                )
                await _audit_rejected(
                    db,
                    ctx=ctx,
                    resource_type=resource_type,
                    code=payload["code"],
                    message=payload["message"],
                )
                await db.commit()
                return payload

            body: dict[str, Any] = {
                "kind": resource_type,
                "pack_id": str(pack.id),
                "slug": pack.slug,
                "relative_path": args.relative_path,
                "current_content_hash": _file_excerpt_hash(target.content_md or ""),
                "rationale": args.rationale,
            }
            summary = (
                f"Evolver proposes remove_file {args.relative_path!r} "
                f"from skill pack {pack.slug!r}"
            )
            approval = await _create_proposal_approval(
                db,
                ctx=ctx,
                config=config,
                resource_type=resource_type,
                resource_id=pack.id,
                body=body,
                summary=summary,
            )
            await audit_svc.record(
                db,
                action=AUDIT_PROPOSED[resource_type],
                actor_identity_id=ctx.identity_id,
                workspace_id=ctx.workspace_id,
                resource_type="skill_pack",
                resource_id=pack.id,
                summary=summary,
                metadata={
                    "approval_id": str(approval.id),
                    "pack_id": str(pack.id),
                    "slug": pack.slug,
                    "relative_path": args.relative_path,
                    "rationale": args.rationale,
                },
            )
            await db.commit()
            return {
                "status": "proposed",
                "kind": resource_type,
                "approval_id": str(approval.id),
                "pack_id": str(pack.id),
                "relative_path": args.relative_path,
                "expires_at": (
                    approval.expires_at.isoformat()
                    if approval.expires_at is not None
                    else None
                ),
            }
        except Exception:  # noqa: BLE001
            log.exception(
                "evolver propose_skill_remove_file failed (workspace=%s pack=%s path=%s)",
                ctx.workspace_id,
                args.pack_id,
                args.relative_path,
            )
            await db.rollback()
            await _bump_breaker_on_internal_error(
                workspace_id=ctx.workspace_id, config=config
            )
            return _rejected(
                "evolver.internal_error",
                "Internal error filing the remove_file proposal; the breaker counter advanced.",
            )


# Avoid unused-import lint when SkillPackVersionState is referenced
# only by the M2.5 dispatch handler downstream of this module.
_ = SkillPackVersionState
_ = compute_content_hash
