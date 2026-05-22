"""Four-layer memory service.

Provides CRUD + char-cap enforcement + render helpers for the three
profile kinds (WORKSPACE_MEMORY, USER_PROFILE, USER_SOUL) and the
approval-gated SOUL write queue. The runtime memory injector at
:mod:`app.agents.harness.memory` calls :func:`render_profile_fragment`
to fold profiles into every Agent system prompt.

Design highlights:

- **Per-kind char caps** (from :mod:`app.db.models.memory_profile`). We
  silently truncate oversized writes at cap — callers don't need to
  pre-trim, and the UI can show a warning by comparing ``char_count`` to
  the configured limit.
- **Upsert-by-scope**. Callers identify a profile by
  ``(workspace_id, kind, subject_id)``; the service lazily creates rows.
- **SOUL proposals**. Direct writes to ``user_soul.content_md`` are
  rejected — callers must go through :func:`propose_soul_update` +
  :func:`decide_soul_update` so every user-model mutation is auditable
  and (by default) requires identity consent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.db.models.memory_profile import (
    MAX_CONTENT_CHARS,
    SOUL_DIMENSIONS,
    MemoryProfile,
    MemoryProfileKind,
)
from app.db.repository import AsyncRepository


# ─── Helpers ──────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _cap_for(kind: MemoryProfileKind) -> int:
    return MAX_CONTENT_CHARS.get(kind, 2000)


def _truncate(text: str, cap: int) -> tuple[str, int]:
    """Truncate ``text`` to at most ``cap`` characters.

    We trim on a paragraph / sentence boundary when possible so the
    visible cut-off looks intentional in the system prompt, but never
    exceed cap by more than a single trailing ``…``.
    """
    body = (text or "").rstrip()
    if len(body) <= cap:
        return body, len(body)
    clipped = body[:cap]
    # Prefer paragraph break, then sentence end, then hard cut.
    for sep in ("\n\n", "\n", ". ", "。", "！", "？"):
        idx = clipped.rfind(sep)
        if idx >= int(cap * 0.6):  # don't chop off too much
            clipped = clipped[: idx + len(sep)].rstrip()
            break
    # Reserve one char for the trailing ellipsis so we never exceed cap.
    clipped = clipped.rstrip()
    if len(clipped) >= cap:
        clipped = clipped[: cap - 1]
    clipped = clipped + "…"
    return clipped, len(clipped)


# ─── CRUD ─────────────────────────────────────────────────
async def get_profile(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: MemoryProfileKind,
    subject_id: uuid.UUID,
) -> MemoryProfile | None:
    stmt = select(MemoryProfile).where(
        and_(
            MemoryProfile.workspace_id == workspace_id,
            MemoryProfile.kind == kind,
            MemoryProfile.subject_id == subject_id,
            MemoryProfile.deleted_at.is_(None),
        )
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def upsert_profile(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    kind: MemoryProfileKind,
    subject_id: uuid.UUID,
    content_md: str,
    soul_dims_json: dict | None = None,
    metadata_json: dict | None = None,
    updated_by: uuid.UUID | None = None,
    _allow_soul_content_overwrite: bool = False,
) -> MemoryProfile:
    """Create-or-replace a profile. Direct writes to ``user_soul.content_md``
    are refused (callers must use :func:`propose_soul_update`); the
    exception exists for :func:`decide_soul_update` to apply approved
    proposals without circular imports.
    """
    if kind == MemoryProfileKind.USER_SOUL and not _allow_soul_content_overwrite:
        raise Conflict(
            "soul_direct_write_forbidden",
            code="memory_profile.soul_direct_write_forbidden",
        )

    clipped, char_count = _truncate(content_md, _cap_for(kind))
    # ``identity_id`` equals ``subject_id`` for identity-scoped kinds so
    # ON DELETE CASCADE works off the FK.
    identity_id = subject_id if kind != MemoryProfileKind.WORKSPACE_MEMORY else None

    existing = await get_profile(
        session, workspace_id=workspace_id, kind=kind, subject_id=subject_id
    )
    if existing is not None:
        patch = {
            "content_md": clipped,
            "char_count": char_count,
            "updated_by": updated_by,
        }
        if soul_dims_json is not None:
            patch["soul_dims_json"] = _sanitize_soul_dims(soul_dims_json)
        if metadata_json is not None:
            patch["metadata_json"] = metadata_json
        return await AsyncRepository(session, MemoryProfile).update(existing, **patch)

    return await AsyncRepository(session, MemoryProfile).create(
        workspace_id=workspace_id,
        kind=kind,
        subject_id=subject_id,
        identity_id=identity_id,
        content_md=clipped,
        char_count=char_count,
        soul_dims_json=_sanitize_soul_dims(soul_dims_json or {}),
        pending_updates_json=[],
        metadata_json=metadata_json or {},
        updated_by=updated_by,
    )


def _sanitize_soul_dims(raw: dict) -> dict:
    """Keep only string-valued entries, and cap each dimension at 400 chars.

    Accepts keys outside :data:`SOUL_DIMENSIONS` (forks may add extra)
    but normalizes them to lowercase snake_case.
    """
    out: dict[str, str] = {}
    for k, v in (raw or {}).items():
        if not isinstance(v, str):
            continue
        key = str(k).strip().lower().replace(" ", "_").replace("-", "_")[:64]
        if not key:
            continue
        out[key] = v.strip()[:400]
    return out


# ─── SOUL approval queue ──────────────────────────────────
@dataclass
class SoulProposal:
    id: str
    proposed_content: str
    proposed_dims: dict
    proposed_at: str
    proposed_by_identity_id: uuid.UUID | None
    source_session_id: uuid.UUID | None
    rationale: str


async def propose_soul_update(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    proposed_content: str,
    proposed_dims: dict | None = None,
    source_session_id: uuid.UUID | None = None,
    proposed_by_identity_id: uuid.UUID | None = None,
    rationale: str = "",
) -> SoulProposal:
    """Queue a SOUL.md rewrite for the given identity. Returns the new
    proposal entry; the profile's ``content_md`` isn't touched until the
    proposal is approved."""
    if not proposed_content or not proposed_content.strip():
        raise ValidationFailed("empty_soul_content", code="memory_profile.empty_soul_content")

    # Upsert an empty SOUL row if this is the first proposal.
    existing = await get_profile(
        session,
        workspace_id=workspace_id,
        kind=MemoryProfileKind.USER_SOUL,
        subject_id=identity_id,
    )
    if existing is None:
        existing = await AsyncRepository(session, MemoryProfile).create(
            workspace_id=workspace_id,
            kind=MemoryProfileKind.USER_SOUL,
            subject_id=identity_id,
            identity_id=identity_id,
            content_md="",
            char_count=0,
            soul_dims_json={},
            pending_updates_json=[],
            metadata_json={},
            updated_by=proposed_by_identity_id,
        )

    clipped, _ = _truncate(proposed_content, _cap_for(MemoryProfileKind.USER_SOUL))
    entry = {
        "id": str(uuid.uuid4()),
        "proposed_content": clipped,
        "proposed_dims": _sanitize_soul_dims(proposed_dims or {}),
        "proposed_at": _now_iso(),
        "proposed_by_identity_id": (
            str(proposed_by_identity_id) if proposed_by_identity_id else None
        ),
        "source_session_id": str(source_session_id) if source_session_id else None,
        "rationale": (rationale or "")[:512],
    }
    pending = list(existing.pending_updates_json or [])
    pending.append(entry)
    # SQLAlchemy needs a new list identity to mark JSONB dirty.
    existing.pending_updates_json = pending
    await session.flush([existing])
    return SoulProposal(
        id=entry["id"],
        proposed_content=entry["proposed_content"],
        proposed_dims=entry["proposed_dims"],
        proposed_at=entry["proposed_at"],
        proposed_by_identity_id=proposed_by_identity_id,
        source_session_id=source_session_id,
        rationale=entry["rationale"],
    )


async def decide_soul_update(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    proposal_id: str,
    approve: bool,
    decided_by: uuid.UUID | None,
    reason: str = "",
) -> MemoryProfile:
    """Approve or reject a queued SOUL proposal. On approval the
    proposal's content replaces ``content_md`` and its dims are merged
    into ``soul_dims_json``."""
    profile = await get_profile(
        session,
        workspace_id=workspace_id,
        kind=MemoryProfileKind.USER_SOUL,
        subject_id=identity_id,
    )
    if profile is None:
        raise NotFound("soul_profile_not_found", code="memory_profile.soul_not_found")

    pending = list(profile.pending_updates_json or [])
    target = next((p for p in pending if p.get("id") == proposal_id), None)
    if target is None:
        raise NotFound("soul_proposal_not_found", code="memory_profile.soul_proposal_not_found")

    remaining = [p for p in pending if p.get("id") != proposal_id]
    history = list(profile.metadata_json.get("decisions", []))
    history.append(
        {
            "proposal_id": proposal_id,
            "decision": "approved" if approve else "rejected",
            "decided_at": _now_iso(),
            "decided_by": str(decided_by) if decided_by else None,
            "reason": (reason or "")[:512],
        }
    )
    meta = {**(profile.metadata_json or {}), "decisions": history[-50:]}

    if approve:
        # Merge dims — new fragments win per dimension.
        merged_dims = {**(profile.soul_dims_json or {}), **(target.get("proposed_dims") or {})}
        return await upsert_profile(
            session,
            workspace_id=workspace_id,
            kind=MemoryProfileKind.USER_SOUL,
            subject_id=identity_id,
            content_md=target.get("proposed_content", ""),
            soul_dims_json=merged_dims,
            metadata_json=meta,
            updated_by=decided_by,
            _allow_soul_content_overwrite=True,
        )

    profile.pending_updates_json = remaining
    profile.metadata_json = meta
    await session.flush([profile])
    return profile


# ─── Injection helpers ────────────────────────────────────
def render_profile_fragment(
    *,
    workspace_memory: MemoryProfile | None,
    user_profile: MemoryProfile | None,
    user_soul: MemoryProfile | None,
) -> str | None:
    """Assemble a markdown block suitable for the Agent system prompt.

    Renders each populated profile under a stable heading so the LLM
    learns to treat them as anchor sections. Empty profiles are skipped.
    Returns ``None`` when nothing is populated so the caller can avoid
    emitting an empty "Profile" section.
    """
    pieces: list[str] = []
    if workspace_memory and (workspace_memory.content_md or "").strip():
        pieces.append("## WORKSPACE MEMORY\n" + workspace_memory.content_md.strip())
    if user_profile and (user_profile.content_md or "").strip():
        pieces.append("## USER PROFILE\n" + user_profile.content_md.strip())
    if user_soul and (user_soul.content_md or "").strip():
        pieces.append("## USER SOUL\n" + user_soul.content_md.strip())
        # Also surface populated dimensions as a compact bullet list so
        # the LLM can steer by structured tags even if it skims the MD.
        dims = user_soul.soul_dims_json or {}
        bullets = [f"- {k}: {v}" for k, v in dims.items() if v]
        if bullets:
            pieces.append("### SOUL DIMENSIONS\n" + "\n".join(bullets))

    if not pieces:
        return None
    return "\n\n".join(pieces)


async def load_profile_fragment(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None,
) -> str | None:
    """Fetch the three-layer profile bundle and render the combined fragment."""
    ws_mem = await get_profile(
        session,
        workspace_id=workspace_id,
        kind=MemoryProfileKind.WORKSPACE_MEMORY,
        subject_id=workspace_id,
    )
    user_prof = None
    user_soul = None
    if identity_id is not None:
        user_prof = await get_profile(
            session,
            workspace_id=workspace_id,
            kind=MemoryProfileKind.USER_PROFILE,
            subject_id=identity_id,
        )
        user_soul = await get_profile(
            session,
            workspace_id=workspace_id,
            kind=MemoryProfileKind.USER_SOUL,
            subject_id=identity_id,
        )
    return render_profile_fragment(
        workspace_memory=ws_mem,
        user_profile=user_prof,
        user_soul=user_soul,
    )


__all__ = [
    "SOUL_DIMENSIONS",
    "SoulProposal",
    "decide_soul_update",
    "get_profile",
    "load_profile_fragment",
    "propose_soul_update",
    "render_profile_fragment",
    "upsert_profile",
]
