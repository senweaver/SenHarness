"""Memory service: store / recall / list / delete with embeddings.

M0.7 additions:
* :func:`apply_payload` — unified entry point that the pending-memory
  promote hook (and the workspace ``allow_immediate`` path) call. It
  validates the payload against the per-workspace memory policy
  (allowed scopes, always-on hard cap), then forwards to
  :func:`store`. Raises :class:`MemoryHardCapExceeded` when the new
  content would push the bucket over the cap.
* :func:`get_workspace_memory_settings` — merged view of platform
  defaults (``system_settings.memory_defaults``) and the per-workspace
  override under ``home_config_json["memory"]``. Lives here (not in
  ``services/workspace.py``) on purpose — keeps the M0.12 subagent's
  workspace service untouched.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness.embedder import embed
from app.core.errors import MemoryHardCapExceeded, NotFound, ValidationFailed
from app.core.security import utcnow_naive
from app.db.models.memory import Memory, MemoryKind, MemoryScope
from app.db.models.workspace import Workspace
from app.repositories.memory import MemoryRepository
from app.services.system_settings import (
    SystemSettingKey,
    get_system_setting,
)


# ─── Workspace memory policy ────────────────────────────────────
@dataclass(frozen=True)
class WorkspaceMemorySettings:
    """Snapshot of the policy that gates memory writes for a workspace.

    ``allow_immediate`` is the M0.7 cache-aware mutation gate; ``False``
    by default so agents must use the deferred path. ``always_on_max_chars``
    is the hard cap (M0.7 + design principle 3) on the total characters
    of memories that get injected into the system prompt for one bucket.
    ``permitted_scopes`` lets a workspace admin lock memorise to a
    subset (e.g. ``["user"]`` only) when policy demands it.
    """

    allow_immediate: bool
    always_on_max_chars: int
    permitted_scopes: tuple[str, ...]
    promotion_max_per_session: int
    max_failure_count_before_skip: int


_DEFAULT_PERMITTED_SCOPES: tuple[str, ...] = ("user", "assistant", "workspace")


async def get_workspace_memory_settings(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> WorkspaceMemorySettings:
    """Return the merged platform-default + workspace-override policy.

    Reads ``system_settings.memory_defaults`` for the platform baseline
    (set in :mod:`app.services.system_settings`) and the workspace's
    ``home_config_json["memory"]`` block for tenant overrides. Missing
    keys fall back to the platform default; never raises.
    """
    platform = await get_system_setting(db, SystemSettingKey.MEMORY_DEFAULTS, default={})
    if not isinstance(platform, dict):
        platform = {}

    ws_overrides: dict = {}
    ws = await db.get(Workspace, workspace_id)
    if ws is not None:
        block = (ws.home_config_json or {}).get("memory")
        if isinstance(block, dict):
            ws_overrides = block

    def _merged(key: str, default):
        if key in ws_overrides:
            return ws_overrides[key]
        if key in platform:
            return platform[key]
        return default

    raw_scopes = _merged("permitted_scopes", list(_DEFAULT_PERMITTED_SCOPES))
    if not isinstance(raw_scopes, (list, tuple)) or not raw_scopes:
        raw_scopes = list(_DEFAULT_PERMITTED_SCOPES)
    scopes = tuple(s for s in raw_scopes if s in {"user", "assistant", "workspace"})
    if not scopes:
        scopes = _DEFAULT_PERMITTED_SCOPES

    return WorkspaceMemorySettings(
        allow_immediate=bool(
            ws_overrides.get(
                "allow_immediate",
                platform.get("allow_immediate_default", False),
            )
        ),
        always_on_max_chars=int(_merged("always_on_max_chars", 4000)),
        permitted_scopes=scopes,
        promotion_max_per_session=int(platform.get("promotion_max_per_session", 50)),
        max_failure_count_before_skip=int(platform.get("max_failure_count_before_skip", 3)),
    )


# ─── apply_payload (M0.7 unified entry point) ───────────────────
async def apply_payload(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    payload: dict,
) -> Memory:
    """Validate + persist a memory write described by ``payload``.

    ``payload`` is the canonical pending-memory tuple:

    .. code-block:: python

        {"content": str, "scope": "user|assistant|workspace",
         "kind": "kv|episodic|semantic", "key": str | None,
         "ttl_seconds": int | None, "confidence": float}

    Failure modes:

    * :class:`ValidationFailed` for unknown / forbidden scope or kind,
      missing ``content``, or KV without ``key``.
    * :class:`MemoryHardCapExceeded` when the destination bucket's
      live total characters + new content would exceed the workspace
      hard cap.

    Returns the :class:`Memory` row on success.
    """
    content = (payload or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValidationFailed("memory_payload_missing_content", code="memory.payload_invalid")

    scope_raw = (payload.get("scope") or "user").lower()
    if scope_raw not in {"user", "assistant", "workspace"}:
        raise ValidationFailed("memory_payload_invalid_scope", code="memory.payload_invalid")

    kind_raw = (payload.get("kind") or "semantic").lower()
    if kind_raw not in {"kv", "episodic", "semantic"}:
        raise ValidationFailed("memory_payload_invalid_kind", code="memory.payload_invalid")

    settings = await get_workspace_memory_settings(db, workspace_id=workspace_id)
    if scope_raw not in settings.permitted_scopes:
        raise ValidationFailed(
            "memory_scope_not_permitted",
            code="memory.scope_not_permitted",
        )

    scope = MemoryScope(scope_raw)
    kind = MemoryKind(kind_raw)
    key = payload.get("key")
    if kind == MemoryKind.KV and not key:
        raise ValidationFailed("memory_kv_requires_key", code="memory.kv_requires_key")

    scope_id = _resolve_scope_id(scope=scope, identity_id=identity_id, agent_id=agent_id)

    await _enforce_hard_cap(
        db,
        workspace_id=workspace_id,
        scope=scope,
        scope_id=scope_id,
        new_content=content,
        existing_key=key if kind == MemoryKind.KV else None,
        max_chars=settings.always_on_max_chars,
    )

    confidence = float(payload.get("confidence") or 1.0)
    confidence = max(0.0, min(1.0, confidence))
    ttl_seconds = payload.get("ttl_seconds")
    ttl = int(ttl_seconds) if ttl_seconds else None

    return await store(
        db,
        workspace_id=workspace_id,
        scope=scope,
        scope_id=scope_id,
        kind=kind,
        key=key,
        content=content,
        ttl_seconds=ttl,
        confidence=confidence,
        source_session_id=_coerce_uuid(payload.get("source_session_id")),
        source_message_id=_coerce_uuid(payload.get("source_message_id")),
        author_identity_id=identity_id,
    )


def _resolve_scope_id(
    *,
    scope: MemoryScope,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
) -> uuid.UUID | None:
    if scope == MemoryScope.USER:
        return identity_id
    if scope == MemoryScope.ASSISTANT:
        return agent_id
    return None


def _coerce_uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


async def _enforce_hard_cap(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    scope: MemoryScope,
    scope_id: uuid.UUID | None,
    new_content: str,
    existing_key: str | None,
    max_chars: int,
) -> None:
    """Block writes that would push the bucket beyond the workspace cap.

    The cap is per ``(workspace, scope, scope_id)`` bucket because that
    is the granularity at which the loader injects always-on memories
    into the system prompt. KV upserts subtract the existing row's
    content length so a content-only refresh under the cap still goes
    through.
    """
    existing_len_q = select(func.coalesce(func.sum(func.length(Memory.content)), 0)).where(
        Memory.workspace_id == workspace_id,
        Memory.scope == scope,
        Memory.deleted_at.is_(None),
    )
    if scope_id is None:
        existing_len_q = existing_len_q.where(Memory.scope_id.is_(None))
    else:
        existing_len_q = existing_len_q.where(Memory.scope_id == scope_id)

    current_total = int((await db.execute(existing_len_q)).scalar() or 0)

    replaced_len = 0
    if existing_key:
        replaced_len_q = select(func.coalesce(func.length(Memory.content), 0)).where(
            Memory.workspace_id == workspace_id,
            Memory.scope == scope,
            Memory.kind == MemoryKind.KV,
            Memory.key == existing_key,
            Memory.deleted_at.is_(None),
        )
        if scope_id is None:
            replaced_len_q = replaced_len_q.where(Memory.scope_id.is_(None))
        else:
            replaced_len_q = replaced_len_q.where(Memory.scope_id == scope_id)
        replaced_len = int((await db.execute(replaced_len_q)).scalar() or 0)

    projected = current_total - replaced_len + len(new_content)
    if projected > max_chars:
        raise MemoryHardCapExceeded(
            f"memory_hard_cap_exceeded:{projected}>{max_chars}",
            code="memory.hard_cap_exceeded",
            extras={
                "current_chars": current_total,
                "new_chars": len(new_content),
                "max_chars": int(max_chars),
            },
        )


async def store(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    scope: MemoryScope,
    scope_id: uuid.UUID | None,
    kind: MemoryKind,
    content: str,
    key: str | None = None,
    value_json: dict | None = None,
    ttl_seconds: int | None = None,
    confidence: float = 1.0,
    source_session_id: uuid.UUID | None = None,
    source_message_id: uuid.UUID | None = None,
    author_identity_id: uuid.UUID | None = None,
) -> Memory:
    """Idempotent for KV memories on (scope, scope_id, kind=kv, key).

    Embedding is computed from ``content`` automatically.
    """
    repo = MemoryRepository(session)

    if kind == MemoryKind.KV and key:
        existing = await repo.get_kv(
            workspace_id=workspace_id,
            scope=scope,
            scope_id=scope_id,
            key=key,
        )
        if existing is not None:
            vec, model_tag = await embed(content, workspace_id=workspace_id)
            ttl_at = _ttl(ttl_seconds)
            await repo.update(
                existing,
                content=content,
                value_json=value_json or {},
                embedding=vec,
                embedding_model=model_tag or None,
                confidence=confidence,
                ttl_at=ttl_at,
                source_message_id=source_message_id,
                source_session_id=source_session_id,
                author_identity_id=author_identity_id,
            )
            return existing

    vec, model_tag = await embed(content, workspace_id=workspace_id)
    return await repo.create(
        workspace_id=workspace_id,
        scope=scope,
        scope_id=scope_id,
        kind=kind,
        key=key,
        content=content,
        value_json=value_json or {},
        embedding=vec,
        embedding_model=model_tag or None,
        confidence=confidence,
        ttl_at=_ttl(ttl_seconds),
        source_message_id=source_message_id,
        source_session_id=source_session_id,
        author_identity_id=author_identity_id,
    )


async def recall(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    query: str,
    limit: int = 6,
    min_score: float = 0.35,
) -> list[tuple[Memory, float]]:
    """Recall memories relevant to ``query`` across user/assistant/workspace scopes.

    Uses embedding cosine similarity when an embedder is configured;
    otherwise falls back directly to a token-overlap scorer so the
    feature still works for workspaces without an embedding-capable
    provider.
    """
    vec, _ = await embed(query, workspace_id=workspace_id)
    scopes: list[tuple[MemoryScope, uuid.UUID | None]] = [
        (MemoryScope.WORKSPACE, None),
    ]
    if identity_id is not None:
        scopes.append((MemoryScope.USER, identity_id))
    if agent_id is not None:
        scopes.append((MemoryScope.ASSISTANT, agent_id))

    repo = MemoryRepository(session)
    primary: list[tuple[Memory, float]] = []
    if vec is not None:
        primary = await repo.recall_by_similarity(
            workspace_id=workspace_id,
            query_embedding=vec,
            scopes=scopes,
            limit=limit,
            min_score=min_score,
        )

    if len(primary) < limit:
        got_ids = {m.id for m, _ in primary}
        fallback = await _token_overlap_recall(
            session,
            workspace_id=workspace_id,
            scopes=scopes,
            query=query,
            limit=limit - len(primary),
            exclude_ids=got_ids,
        )
        primary.extend(fallback)

    return primary[:limit]


async def _token_overlap_recall(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    scopes: list[tuple[MemoryScope, uuid.UUID | None]],
    query: str,
    limit: int,
    exclude_ids: set,
) -> list[tuple[Memory, float]]:
    tokens = _tokenize(query)
    if not tokens:
        return []

    from sqlalchemy import or_

    scope_clauses = [
        (Memory.scope == s) & (Memory.scope_id.is_(None) if sid is None else Memory.scope_id == sid)
        for s, sid in scopes
    ]
    stmt = select(Memory).where(
        Memory.workspace_id == workspace_id,
        Memory.deleted_at.is_(None),
        or_(*scope_clauses),
    )
    rows = (await session.execute(stmt)).scalars().all()

    scored: list[tuple[Memory, float]] = []
    for mem in rows:
        if mem.id in exclude_ids:
            continue
        mem_tokens = _tokenize(mem.content) | _tokenize(mem.key or "")
        if not mem_tokens:
            continue
        overlap = len(tokens & mem_tokens)
        if overlap == 0:
            continue
        score = overlap / max(1, len(tokens))
        scored.append((mem, min(0.95, 0.4 + score * 0.5)))

    scored.sort(key=lambda p: p[1], reverse=True)
    return scored[:limit]


def _tokenize(text: str) -> set[str]:
    """Basic normalization: lowercase + split on non-alnum. Works for EN; for CN
    we also yield 2-grams so Chinese partial matches still score."""
    import re

    t = (text or "").lower().strip()
    if not t:
        return set()
    words = set(re.findall(r"[a-z0-9]+", t))
    cn = re.findall(r"[\u4e00-\u9fff]+", t)
    for run in cn:
        for i in range(len(run) - 1):
            words.add(run[i : i + 2])
        if len(run) == 1:
            words.add(run)
    return {w for w in words if len(w) >= 2 or (len(w) == 1 and 0x4E00 <= ord(w[0]) <= 0x9FFF)}


async def forget(session: AsyncSession, *, workspace_id: uuid.UUID, memory_id: uuid.UUID) -> None:
    repo = MemoryRepository(session)
    mem = await repo.get(memory_id)
    if mem is None or mem.workspace_id != workspace_id:
        raise NotFound("memory_not_found", code="memory.not_found")
    await repo.soft_delete(mem)


def _ttl(seconds: int | None) -> datetime | None:
    if not seconds:
        return None
    return utcnow_naive() + timedelta(seconds=seconds)
