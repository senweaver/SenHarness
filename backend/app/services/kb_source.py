"""KB source CRUD + sync orchestration + document ACL resolution.

The sync orchestrator drives the configured :class:`KbConnector`, turning
each :class:`ConnectorDocument` into a ``KnowledgeDoc`` (reusing the
existing ``knowledge.ingest_document`` pipeline for URL / text payloads
and ``knowledge.ingest_attachment`` for uploaded files). Progress events
are both persisted on the sync row (``events_json``) *and* yielded to
the caller so the HTTP SSE endpoint can surface a live log.

ACL resolution lives here too — the search path filters chunks through
:func:`filter_accessible_doc_ids` so the Agent never retrieves a doc the
caller isn't entitled to see.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models.kb_source import (
    KbAccess,
    KbAccessLevel,
    KbAccessSubjectKind,
    KbSource,
    KbSourceStatus,
    KbSourceSync,
    KbSyncStatus,
)
from app.db.models.knowledge import DocSourceKind, KnowledgeCollection
from app.db.models.membership import Membership, MembershipStatus
from app.db.repository import AsyncRepository
from app.services import attachment as att_svc
from app.services import knowledge as knowledge_svc
from app.services.kb_connectors import (
    ConnectorDocument,
    SyncProgressEvent,
    get_connector,
)

log = logging.getLogger(__name__)


# ─── Source CRUD ──────────────────────────────────────────
async def get_source_or_404(
    session: AsyncSession, source_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> KbSource:
    row = await AsyncRepository(session, KbSource).get(source_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        raise NotFound("kb_source_not_found", code="kb.source_not_found")
    return row


async def list_sources_for_collection(
    session: AsyncSession, *, workspace_id: uuid.UUID, collection_id: uuid.UUID
) -> list[KbSource]:
    stmt = (
        select(KbSource)
        .where(
            KbSource.workspace_id == workspace_id,
            KbSource.collection_id == collection_id,
            KbSource.deleted_at.is_(None),
        )
        .order_by(desc(KbSource.created_at))
    )
    return list((await session.execute(stmt)).scalars().all())


# ─── Sync ──────────────────────────────────────────────────
@dataclass
class SyncUpdate:
    """One frame in the SSE stream."""

    kind: str  # one of: started | progress | doc | done | failed
    payload: dict


async def run_sync(
    session: AsyncSession,
    *,
    source: KbSource,
    collection: KnowledgeCollection,
    started_by: uuid.UUID | None,
) -> AsyncIterator[SyncUpdate]:
    """Execute one sync pass for ``source``.

    The caller receives :class:`SyncUpdate` frames suitable for SSE. DB
    commits happen at safe boundaries (sync row creation, per-doc ingest,
    final status) so a client disconnect doesn't leak transactions.
    """
    sync_repo = AsyncRepository(session, KbSourceSync)
    source_repo = AsyncRepository(session, KbSource)

    try:
        connector = get_connector(source.kind)
    except KeyError as exc:
        yield SyncUpdate(kind="failed", payload={"error": str(exc)})
        return

    try:
        connector.validate_config(source.config_json or {})
    except ValueError as exc:
        yield SyncUpdate(kind="failed", payload={"error": str(exc)})
        return

    sync_row = await sync_repo.create(
        source_id=source.id,
        status=KbSyncStatus.RUNNING,
        events_json=[],
        started_by=started_by,
    )
    await source_repo.update(source, status=KbSourceStatus.SYNCING, last_error=None)
    await session.commit()
    await session.refresh(sync_row)
    await session.refresh(source)

    yield SyncUpdate(
        kind="started",
        payload={"sync_id": str(sync_row.id), "source_id": str(source.id)},
    )

    docs_added = 0
    docs_failed = 0
    chunks_total = 0
    events: list[dict] = []

    try:
        async for item in connector.sync(config=source.config_json or {}):
            if isinstance(item, SyncProgressEvent):
                evt = {
                    "ts": _utcnow_iso(),
                    "level": item.level,
                    "msg": item.msg,
                    **({"data": item.data} if item.data else {}),
                }
                events.append(evt)
                yield SyncUpdate(kind="progress", payload=evt)
                continue

            # ConnectorDocument — run the ingest and surface per-doc stats.
            try:
                result = await _ingest_one(
                    session,
                    collection=collection,
                    doc=item,
                    created_by=started_by,
                )
            except Exception as exc:  # per-doc failures shouldn't kill sync
                docs_failed += 1
                msg = f"ingest failed for {item.title!r}: {exc}"
                log.exception("kb sync doc failure source=%s", source.id)
                evt = {"ts": _utcnow_iso(), "level": "error", "msg": msg}
                events.append(evt)
                yield SyncUpdate(kind="progress", payload=evt)
                continue

            if result is None:
                continue

            docs_added += 1
            chunks_total += result.chunks
            yield SyncUpdate(
                kind="doc",
                payload={
                    "title": item.title,
                    "doc_id": str(result.doc.id),
                    "chunks": result.chunks,
                    "status": result.doc.status,
                },
            )
        status = KbSyncStatus.SUCCEEDED
        error_text: str | None = None
    except Exception as exc:
        log.exception("kb sync crashed source=%s", source.id)
        status = KbSyncStatus.FAILED
        error_text = str(exc)[:2000]
        yield SyncUpdate(kind="progress", payload={"level": "error", "msg": error_text})

    # Persist final outcome.
    await sync_repo.update(
        sync_row,
        status=status,
        docs_added=docs_added,
        docs_failed=docs_failed,
        chunks_total=chunks_total,
        error_text=error_text,
        events_json=events,
    )
    await source_repo.update(
        source,
        status=(
            KbSourceStatus.READY if status == KbSyncStatus.SUCCEEDED else KbSourceStatus.FAILED
        ),
        last_synced_at=_utcnow_iso(),
        last_error=error_text,
        doc_count=(source.doc_count or 0) + docs_added,
    )
    await session.commit()
    await session.refresh(sync_row)

    yield SyncUpdate(
        kind="done" if status == KbSyncStatus.SUCCEEDED else "failed",
        payload={
            "sync_id": str(sync_row.id),
            "status": status.value,
            "docs_added": docs_added,
            "docs_failed": docs_failed,
            "chunks_total": chunks_total,
            "error": error_text,
        },
    )


async def _ingest_one(
    session: AsyncSession,
    *,
    collection: KnowledgeCollection,
    doc: ConnectorDocument,
    created_by: uuid.UUID | None,
) -> knowledge_svc.IngestResult | None:
    """Dispatch a :class:`ConnectorDocument` to the right ingest primitive."""
    # File connector → load attachment + run attachment extractor.
    if (
        doc.source_kind == DocSourceKind.FILE
        and doc.source_uri
        and doc.source_uri.startswith("attachment://")
    ):
        try:
            att_id = uuid.UUID(doc.source_uri.split("://", 1)[1])
        except ValueError as exc:
            raise RuntimeError(f"invalid attachment uri: {doc.source_uri}") from exc
        att = await att_svc.get_for_read(
            session, attachment_id=att_id, workspace_id=collection.workspace_id
        )
        data = att_svc.read_bytes(att)
        return await knowledge_svc.ingest_attachment(
            session,
            collection=collection,
            attachment=att,
            data=data,
            created_by=created_by,
            title_override=doc.title,
        )

    # URL / text payloads go through the same inline pipeline the
    # ``/docs`` route uses.
    return await knowledge_svc.ingest_document(
        session,
        collection=collection,
        title=doc.title,
        source_kind=doc.source_kind,
        source_uri=doc.source_uri,
        raw_text=doc.raw_text,
        metadata_json={
            **doc.metadata,
            **({"external_id": doc.external_id} if doc.external_id else {}),
        },
        created_by=created_by,
    )


# ─── SSE helpers ──────────────────────────────────────────
async def stream_sse(
    stream: AsyncIterator[SyncUpdate],
) -> AsyncIterator[bytes]:
    """Encode a ``SyncUpdate`` stream as SSE ``data:`` frames.

    Emits a ``: keepalive`` comment every 15 seconds so intermediaries
    (nginx, ALB) don't time out long-running syncs.
    """
    it = stream.__aiter__()
    while True:
        try:
            update = await asyncio.wait_for(it.__anext__(), timeout=15.0)
        except StopAsyncIteration:
            return
        except TimeoutError:
            yield b": keepalive\n\n"
            continue
        body = json.dumps(
            {"kind": update.kind, **update.payload},
            ensure_ascii=False,
            default=str,
        )
        yield f"event: {update.kind}\ndata: {body}\n\n".encode()


# ─── Access control ────────────────────────────────────────
async def filter_accessible_doc_ids(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    collection_id: uuid.UUID,
    candidate_doc_ids: list[uuid.UUID] | None = None,
) -> set[uuid.UUID] | None:
    """Return the set of doc ids the caller can read in ``collection_id``.

    Returns ``None`` when no restrictions apply (collection is open to
    every workspace member). An empty set means "nothing visible".
    """
    rows = await list_access_entries(session, collection_id=collection_id)
    if not rows:
        return None  # default open-to-workspace

    # Collect caller's principals.
    mem = (
        await session.execute(
            select(Membership).where(
                Membership.workspace_id == workspace_id,
                Membership.identity_id == identity_id,
                Membership.status == MembershipStatus.ACTIVE,
                Membership.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    identity_set: set[tuple[KbAccessSubjectKind, uuid.UUID]] = set()
    if mem is not None:
        identity_set.add((KbAccessSubjectKind.IDENTITY, identity_id))
        identity_set.add((KbAccessSubjectKind.WORKSPACE, workspace_id))
        if mem.department_id is not None:
            identity_set.add((KbAccessSubjectKind.DEPARTMENT, mem.department_id))

    # Quick-out: any collection-level grant to a principal we hold?
    collection_open = False
    for row in rows:
        if row.doc_id is None and (row.subject_kind, row.subject_id) in identity_set:
            collection_open = True
            break

    # If the collection is open the caller sees every doc unless a
    # doc-level entry revokes or narrows (we treat doc-level entries as
    # *additional* grants, not revocations — call admin UI handles
    # explicit revocations by deleting the collection-level grant).
    if collection_open and candidate_doc_ids is None:
        return None
    if collection_open:
        return set(candidate_doc_ids or [])

    # Otherwise collect every doc-level grant we hold.
    allowed: set[uuid.UUID] = set()
    for row in rows:
        if row.doc_id is None:
            continue
        if (row.subject_kind, row.subject_id) in identity_set:
            allowed.add(row.doc_id)
    if candidate_doc_ids is not None:
        allowed &= set(candidate_doc_ids)
    return allowed


async def list_access_entries(session: AsyncSession, *, collection_id: uuid.UUID) -> list[KbAccess]:
    stmt = (
        select(KbAccess)
        .where(
            KbAccess.collection_id == collection_id,
            KbAccess.deleted_at.is_(None),
        )
        .order_by(KbAccess.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def grant_access(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    collection_id: uuid.UUID,
    doc_id: uuid.UUID | None,
    subject_kind: KbAccessSubjectKind,
    subject_id: uuid.UUID,
    level: KbAccessLevel,
    granted_by: uuid.UUID | None,
) -> KbAccess:
    # Upsert-ish: if an (doc_id, subject_kind, subject_id) already
    # exists (active) we just return it — admins shouldn't get 409
    # for re-granting.
    existing = (
        await session.execute(
            select(KbAccess).where(
                KbAccess.collection_id == collection_id,
                KbAccess.deleted_at.is_(None),
                KbAccess.subject_kind == subject_kind,
                KbAccess.subject_id == subject_id,
                (KbAccess.doc_id.is_(None) if doc_id is None else KbAccess.doc_id == doc_id),
            )
        )
    ).scalar_one_or_none()
    repo = AsyncRepository(session, KbAccess)
    if existing is not None:
        if existing.level != level:
            existing = await repo.update(existing, level=level)
        return existing
    return await repo.create(
        workspace_id=workspace_id,
        collection_id=collection_id,
        doc_id=doc_id,
        subject_kind=subject_kind,
        subject_id=subject_id,
        level=level,
        granted_by=granted_by,
    )


async def revoke_access(
    session: AsyncSession, *, access_id: uuid.UUID, workspace_id: uuid.UUID
) -> None:
    row = await AsyncRepository(session, KbAccess).get(access_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        raise NotFound("kb_access_not_found", code="kb.access_not_found")
    await AsyncRepository(session, KbAccess).soft_delete(row)


# ─── Helpers ──────────────────────────────────────────────
def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# Exported for tests.
__all__ = [
    "SyncUpdate",
    "filter_accessible_doc_ids",
    "get_source_or_404",
    "grant_access",
    "list_access_entries",
    "list_sources_for_collection",
    "revoke_access",
    "run_sync",
    "stream_sse",
]


# Silence unused import linter — ``and_`` / ``or_`` kept for future
# filtering expansions in ACL queries.
_ = (and_, or_)
