"""Hub promotion preview / staging (M3.2 service-only entry point).

Lifecycle of a workspace SkillPack version becoming a hub version:

1. **Preview** (M3.2 — this module). Read source pack, run sanitizer,
   hash run ids, look up dedup target, list blockers. **No commit.**
2. **Promote** (M3.3, future). Re-run preview inside the same
   transaction, refuse if blockers are non-empty, insert
   :class:`HubSkillPack` (or reuse existing) + :class:`HubSkillPackVersion`,
   write ``hub.skill_pack.created`` audit, attach
   ``WorkspaceHubSubscription`` for the source workspace.
3. **Pull** (M3.3, future). Reverse direction: hub version → workspace
   ``CANDIDATE`` ``SkillPackVersion`` (still flows through M2 approval).

M3.2 lands the **preview** half so M3.3 can compose its promote verb
on top without writing a parallel sanitization path. The preview is a
pure, read-only read-out of "what would happen if I clicked Promote" —
the M3.3 wire-up will be a thin commit wrapper.

Interaction with ``HubSettings.sanitizer_required``
---------------------------------------------------

The setting is checked **here**, not inside :func:`sanitize_for_hub`.
The sanitizer never throws when a regex bug aborts the body rewrite
mid-flight; it returns a :class:`SanitizedHubPayload` with
``stats.failure_reason`` set. This module reads that field, the
``sanitizer_required`` knob, and the caller's scope eligibility, and
returns a ``blockers`` list. M3.3 will refuse to commit when the list
is non-empty; M3.2 only emits an audit row that the preview was run.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.hub_skill_pack import HubScope
from app.db.models.hub_skill_pack_version import HubSkillPackVersion
from app.db.models.identity import Identity
from app.db.models.skill_pack_version import SkillPackVersion
from app.db.models.skills import SkillPack
from app.db.models.workspace import Workspace
from app.repositories.hub_skill_pack import (
    HubSkillPackRepository,
    HubSkillPackVersionRepository,
)
from app.repositories.skill_pack_version import SkillPackVersionRepository
from app.repositories.skills import SkillFileRepository, SkillPackRepository
from app.repositories.workspace import WorkspaceRepository
from app.services import audit as audit_svc
from app.services import hub_skill as hub_svc
from app.services.skill_sanitize import (
    SanitizedHubPayload,
    sanitize_for_hub,
)
from app.services.skill_sanitize_pii import build_pii_detector_for_workspace
from app.services.skill_version import compute_content_hash

__all__ = [
    "BLOCKER_PACK_NOT_FOUND",
    "BLOCKER_PACK_NOT_OWNED",
    "BLOCKER_SANITIZER_REQUIRED_FAILED",
    "BLOCKER_SCOPE_PERMISSION_DENIED",
    "BLOCKER_SLUG_TOMBSTONED",
    "BLOCKER_VERSION_NOT_FOUND",
    "HubPromotionInput",
    "HubPromotionPreview",
    "preview_promotion",
]

log = logging.getLogger(__name__)


# ── Stable blocker keys ──────────────────────────────────────
BLOCKER_PACK_NOT_FOUND = "pack_not_found"
BLOCKER_PACK_NOT_OWNED = "pack_not_owned_by_workspace"
BLOCKER_VERSION_NOT_FOUND = "version_not_found"
BLOCKER_SCOPE_PERMISSION_DENIED = "scope_permission_denied"
BLOCKER_SANITIZER_REQUIRED_FAILED = "sanitizer_required_failed"
BLOCKER_SLUG_TOMBSTONED = "slug_tombstoned"


# ── DTOs ─────────────────────────────────────────────────────
@dataclass
class HubPromotionInput:
    workspace_id: uuid.UUID
    pack_id: uuid.UUID
    target_scope: HubScope
    version_id: uuid.UUID | None = None
    target_slug: str | None = None


@dataclass
class HubPromotionPreview:
    sanitized: SanitizedHubPayload
    target_slug: str
    target_tenant_id: uuid.UUID | None
    target_scope: HubScope
    will_dedup_against: HubSkillPackVersion | None
    sanitized_content_hash: str
    blockers: list[str] = field(default_factory=list)
    sanitizer_required: bool = True
    pii_detector_active: bool = False
    # Sanitized ancillary file map. Populated by the same M3.2 pass
    # that produces ``sanitized_content_hash`` so the M3.3 apply step
    # can persist the exact bytes that contributed to the hash on
    # the hub version's ``files_json``. Empty dict when the source
    # pack has no files.
    sanitized_files: dict[str, str] = field(default_factory=dict)

    @property
    def is_promotable(self) -> bool:
        return not self.blockers


# ── Preview ──────────────────────────────────────────────────
async def preview_promotion(
    db: AsyncSession,
    *,
    request: HubPromotionInput,
    actor_identity: Identity,
    audit_request: Any = None,
) -> HubPromotionPreview:
    """Stage a hub promotion without writing any rows.

    Side effects:

    * One audit row, ``hub.sanitize.previewed``, summarising the
      stats. When ``HubSettings.sanitizer_required`` is True and the
      sanitizer reported a failure, an extra
      ``hub.sanitize.blocked_by_required`` row lands.
    * No DB inserts / updates / commits.
    """
    settings = await hub_svc.get_hub_settings(db)
    sanitizer_required = bool(getattr(settings, "sanitizer_required", True))

    workspace = await WorkspaceRepository(db).get(request.workspace_id)
    if workspace is None:
        raise NotFound("workspace_not_found", code="workspace.not_found")

    pack = await SkillPackRepository(db).get(request.pack_id)
    blockers: list[str] = []

    if pack is None:
        return _empty_preview_with_blocker(
            request=request,
            blocker=BLOCKER_PACK_NOT_FOUND,
            sanitizer_required=sanitizer_required,
        )

    if pack.workspace_id != workspace.id:
        return _empty_preview_with_blocker(
            request=request,
            blocker=BLOCKER_PACK_NOT_OWNED,
            sanitizer_required=sanitizer_required,
        )

    version = await _resolve_source_version(
        db, workspace_id=workspace.id, pack=pack, version_id=request.version_id
    )
    if version is None:
        return _empty_preview_with_blocker(
            request=request,
            blocker=BLOCKER_VERSION_NOT_FOUND,
            sanitizer_required=sanitizer_required,
        )

    if not await hub_svc.is_caller_eligible_for_scope(actor_identity, request.target_scope):
        blockers.append(BLOCKER_SCOPE_PERMISSION_DENIED)

    target_tenant_id: uuid.UUID | None
    if request.target_scope == HubScope.PLATFORM:
        target_tenant_id = None
    else:
        target_tenant_id = await hub_svc.resolve_caller_tenant(db, workspace_id=workspace.id)

    target_slug = (request.target_slug or pack.slug).strip()

    if await hub_svc.is_hub_slug_tombstoned(
        db,
        scope=request.target_scope,
        tenant_id=target_tenant_id,
        slug=target_slug,
    ):
        blockers.append(BLOCKER_SLUG_TOMBSTONED)

    workspace_settings = _hub_promotion_settings(workspace)
    detector = None
    if not workspace_settings.skip_pii_detection:
        detector = await build_pii_detector_for_workspace(workspace.id, db)

    files_payload = await _load_pack_files(
        db,
        workspace_id=workspace.id,
        pack_id=pack.id,
        version_files=version.files_json,
    )

    sanitized = sanitize_for_hub(
        version.content_md or "",
        version.source_run_ids or [],
        workspace_slug=workspace.slug,
        workspace_id=workspace.id,
        pii_detector_fn=detector,
        extra_redaction_patterns=workspace_settings.extra_redaction_patterns,
        skip_pii_detection=workspace_settings.skip_pii_detection,
    )

    if sanitized.stats.failure_reason is not None and sanitizer_required:
        blockers.append(BLOCKER_SANITIZER_REQUIRED_FAILED)

    sanitized_files = _sanitize_files(
        files_payload,
        workspace_slug=workspace.slug,
        workspace_id=workspace.id,
        pii_detector_fn=detector,
        extra_redaction_patterns=workspace_settings.extra_redaction_patterns,
        skip_pii_detection=workspace_settings.skip_pii_detection,
    )
    sanitized_content_hash = compute_content_hash(sanitized.content_md, sanitized_files)

    will_dedup_against = await _lookup_existing_hub_version(
        db,
        scope=request.target_scope,
        tenant_id=target_tenant_id,
        slug=target_slug,
        sanitized_content_hash=sanitized_content_hash,
    )

    preview = HubPromotionPreview(
        sanitized=sanitized,
        target_slug=target_slug,
        target_tenant_id=target_tenant_id,
        target_scope=request.target_scope,
        will_dedup_against=will_dedup_against,
        sanitized_content_hash=sanitized_content_hash,
        blockers=blockers,
        sanitizer_required=sanitizer_required,
        pii_detector_active=detector is not None,
        sanitized_files=sanitized_files,
    )

    await _record_preview_audit(
        db,
        actor=actor_identity,
        workspace=workspace,
        pack=pack,
        version=version,
        preview=preview,
        request=audit_request,
    )
    if sanitized.stats.failure_reason is not None and sanitizer_required:
        await audit_svc.record(
            db,
            action="hub.sanitize.blocked_by_required",
            actor_identity_id=actor_identity.id,
            workspace_id=workspace.id,
            resource_type="skill_pack_version",
            resource_id=version.id,
            summary=(f"hub promote blocked: sanitizer failed ({sanitized.stats.failure_reason})"),
            metadata={
                "pack_id": str(pack.id),
                "version_id": str(version.id),
                "failure_reason": sanitized.stats.failure_reason,
                "target_scope": request.target_scope.value,
                "target_slug": target_slug,
            },
            request=audit_request,
        )

    return preview


# ── Internals ────────────────────────────────────────────────
@dataclass
class _HubPromotionSettings:
    extra_redaction_patterns: list[str] = field(default_factory=list)
    skip_pii_detection: bool = False


def _hub_promotion_settings(workspace: Workspace) -> _HubPromotionSettings:
    raw = (workspace.home_config_json or {}).get("hub_promotion") or {}
    if not isinstance(raw, dict):
        return _HubPromotionSettings()
    patterns = raw.get("extra_redaction_patterns") or []
    if not isinstance(patterns, list):
        patterns = []
    skip = bool(raw.get("skip_pii_detection", False))
    return _HubPromotionSettings(
        extra_redaction_patterns=[str(p) for p in patterns if isinstance(p, str)],
        skip_pii_detection=skip,
    )


def _empty_preview_with_blocker(
    *,
    request: HubPromotionInput,
    blocker: str,
    sanitizer_required: bool,
) -> HubPromotionPreview:
    return HubPromotionPreview(
        sanitized=SanitizedHubPayload(content_md=""),
        target_slug=(request.target_slug or "").strip(),
        target_tenant_id=None,
        target_scope=request.target_scope,
        will_dedup_against=None,
        sanitized_content_hash="",
        blockers=[blocker],
        sanitizer_required=sanitizer_required,
        pii_detector_active=False,
        sanitized_files={},
    )


async def _resolve_source_version(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack: SkillPack,
    version_id: uuid.UUID | None,
) -> SkillPackVersion | None:
    repo = SkillPackVersionRepository(db)
    if version_id is None:
        active = await repo.get_active(workspace_id=workspace_id, pack_id=pack.id)
        if active is not None:
            return active
        return await repo.get_latest(workspace_id=workspace_id, pack_id=pack.id)
    version = await repo.get(version_id)
    if version is None or version.workspace_id != workspace_id:
        return None
    if version.pack_id != pack.id:
        return None
    return version


async def _load_pack_files(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    pack_id: uuid.UUID,
    version_files: Mapping[str, Any] | None,
) -> dict[str, str]:
    """Merge version-frozen files with the live SkillFile rows.

    The hub upload prefers the version-frozen ``files_json`` because
    it survives changes to the live ``SkillFile`` rows after the
    snapshot was taken. If a version doesn't carry any files yet
    (legacy rows), fall back to the live SkillFile table so the
    sanitized hub payload still includes the ancillary files.
    """
    files: dict[str, str] = {}
    if version_files:
        for path, content in version_files.items():
            if isinstance(path, str) and isinstance(content, str):
                files[path] = content
    if files:
        return files

    repo = SkillFileRepository(db)
    rows = await repo.list_for_pack(workspace_id=workspace_id, skill_pack_id=pack_id)
    return {row.path: row.content_md or "" for row in rows}


def _sanitize_files(
    files: Mapping[str, str],
    *,
    workspace_slug: str,
    workspace_id: uuid.UUID,
    pii_detector_fn: Any,
    extra_redaction_patterns: list[str],
    skip_pii_detection: bool,
) -> dict[str, str]:
    """Run the same sanitizer pass on each ancillary file body.

    Path keys are sanitized as plain text so a slug embedded in a
    relative path doesn't sneak through. The body uses the full
    sanitizer; the path uses only the workspace-slug rewrite because
    treating a path as a markdown body would noisily rewrite its
    legitimate file extension.
    """
    sanitized_files: dict[str, str] = {}
    for path, content in files.items():
        body_payload = sanitize_for_hub(
            content,
            [],
            workspace_slug=workspace_slug,
            workspace_id=workspace_id,
            pii_detector_fn=pii_detector_fn,
            extra_redaction_patterns=extra_redaction_patterns,
            skip_pii_detection=skip_pii_detection,
        )
        sanitized_path = sanitize_for_hub(
            path,
            [],
            workspace_slug=workspace_slug,
            workspace_id=workspace_id,
            pii_detector_fn=None,
            extra_redaction_patterns=None,
            skip_pii_detection=True,
        ).content_md
        sanitized_files[sanitized_path] = body_payload.content_md
    return sanitized_files


async def _lookup_existing_hub_version(
    db: AsyncSession,
    *,
    scope: HubScope,
    tenant_id: uuid.UUID | None,
    slug: str,
    sanitized_content_hash: str,
) -> HubSkillPackVersion | None:
    if not sanitized_content_hash:
        return None
    pack = await HubSkillPackRepository(db).get_by_slug(scope=scope, tenant_id=tenant_id, slug=slug)
    if pack is None:
        return None
    return await HubSkillPackVersionRepository(db).find_by_hash(
        hub_pack_id=pack.id, content_hash=sanitized_content_hash
    )


async def _record_preview_audit(
    db: AsyncSession,
    *,
    actor: Identity,
    workspace: Workspace,
    pack: SkillPack,
    version: SkillPackVersion,
    preview: HubPromotionPreview,
    request: Any,
) -> None:
    stats = preview.sanitized.stats
    metadata = {
        "pack_id": str(pack.id),
        "pack_slug": pack.slug,
        "version_id": str(version.id),
        "version_no": version.version_no,
        "target_scope": preview.target_scope.value,
        "target_slug": preview.target_slug,
        "target_tenant_id": (
            str(preview.target_tenant_id) if preview.target_tenant_id is not None else None
        ),
        "redacted_emails": stats.redacted_emails,
        "redacted_urls": stats.redacted_urls,
        "redacted_paths": stats.redacted_paths,
        "redacted_pii": stats.redacted_pii,
        "redacted_extra": stats.redacted_extra,
        "run_id_hashed_count": stats.run_id_hashed_count,
        "failure_reason": stats.failure_reason,
        "sanitizer_required": preview.sanitizer_required,
        "pii_detector_active": preview.pii_detector_active,
        "will_dedup_against_version_id": (
            str(preview.will_dedup_against.id) if preview.will_dedup_against is not None else None
        ),
        "blockers": list(preview.blockers),
    }

    if stats.failure_reason is not None:
        action = "hub.sanitize.failed"
        summary = f"hub sanitize preview failed for {pack.slug!r}: {stats.failure_reason}"
    else:
        action = "hub.sanitize.previewed"
        summary = (
            f"hub sanitize preview for {pack.slug!r} v{version.version_no} "
            f"→ scope={preview.target_scope.value}"
        )

    await audit_svc.record(
        db,
        action=action,
        actor_identity_id=actor.id,
        workspace_id=workspace.id,
        resource_type="skill_pack_version",
        resource_id=version.id,
        summary=summary,
        metadata=metadata,
        request=request,
    )
