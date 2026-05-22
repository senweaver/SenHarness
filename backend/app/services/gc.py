"""Nightly garbage collector — sweeps stale soft-deleted rows + on-disk blobs.

Targets:

* **Attachments**: soft-deleted > ``ATTACHMENT_GC_DAYS`` → hard-delete row +
  remove the local file. (S3/OSS blobs are skipped — those backends rely on
  the bucket lifecycle policy instead.)
* **Knowledge docs**: soft-deleted > ``KNOWLEDGE_DOC_GC_DAYS`` → hard-delete
  row; chunks were already removed by the user-facing delete endpoint.
* **Approvals**: any row that's not ``pending`` and older than
  ``APPROVAL_RETENTION_DAYS`` → hard delete. Audit row history is the
  long-term ledger; the approvals table is just runtime state.
* **Audit events**: older than ``AUDIT_RETENTION_DAYS`` → hard delete.

Runs from APScheduler (``workflows/scheduler.py``) at 03:00 UTC daily, and
exposed via ``POST /admin/gc/run`` for on-demand execution + dry runs.

All sweeps return a dict so the admin endpoint can echo "would delete N" /
"deleted N" cleanly.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.attachment import Attachment
from app.db.models.audit import AuditEvent
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDoc
from app.db.session import get_session_factory

log = logging.getLogger(__name__)


# ─── Public entry points ───────────────────────────────────
async def run_full_sweep(*, dry_run: bool = False) -> dict[str, Any]:
    """Run every sweep in sequence. Returns a per-sweep summary."""
    factory = get_session_factory()
    summary: dict[str, Any] = {"dry_run": dry_run}
    async with factory() as db:
        summary["attachments"] = await sweep_attachments(db, dry_run=dry_run)
        summary["knowledge_docs"] = await sweep_knowledge_docs(db, dry_run=dry_run)
        summary["approvals"] = await sweep_approvals(db, dry_run=dry_run)
        summary["audit_events"] = await sweep_audit_events(db, dry_run=dry_run)
        if not dry_run:
            await db.commit()
    log.info("gc sweep completed: %s", summary)
    return summary


# ─── Individual sweeps ─────────────────────────────────────
async def sweep_attachments(session: AsyncSession, *, dry_run: bool) -> dict[str, Any]:
    """Hard-delete attachments soft-deleted past the retention window.

    Removes the on-disk blob too — but only for the local backend. S3/OSS
    deployments should use bucket lifecycle policies; we skip storage cleanup
    there to avoid coupling the GC to an SDK we haven't pinned yet.
    """
    days = settings.ATTACHMENT_GC_DAYS
    if days <= 0:
        return {"skipped": True, "reason": "disabled"}
    cutoff = utcnow_naive() - timedelta(days=days)
    stmt = (
        select(Attachment)
        .where(Attachment.deleted_at.is_not(None))
        .where(Attachment.deleted_at < cutoff)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    blobs_removed = 0
    if not dry_run:
        for row in rows:
            if settings.STORAGE_BACKEND == "local" and row.storage_uri:
                try:
                    p = Path(row.storage_uri)
                    if p.exists():
                        p.unlink()
                        blobs_removed += 1
                except OSError as e:  # pragma: no cover
                    log.warning("gc: blob unlink failed for %s: %s", row.id, e)
            await session.delete(row)
    return {
        "candidates": len(rows),
        "deleted": 0 if dry_run else len(rows),
        "blobs_removed": blobs_removed,
        "cutoff": cutoff.isoformat(),
    }


async def sweep_knowledge_docs(session: AsyncSession, *, dry_run: bool) -> dict[str, Any]:
    days = settings.KNOWLEDGE_DOC_GC_DAYS
    if days <= 0:
        return {"skipped": True, "reason": "disabled"}
    cutoff = utcnow_naive() - timedelta(days=days)
    # Pre-count for the dry-run summary.
    count_stmt = select(KnowledgeDoc.id).where(
        KnowledgeDoc.deleted_at.is_not(None),
        KnowledgeDoc.deleted_at < cutoff,
    )
    ids = [r[0] for r in (await session.execute(count_stmt)).all()]
    if not dry_run and ids:
        # Belt-and-suspenders: chunks are deleted by the user-facing endpoint
        # but we re-issue here in case a code path forgot.
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id.in_(ids)))
        await session.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id.in_(ids)))
    return {
        "candidates": len(ids),
        "deleted": 0 if dry_run else len(ids),
        "cutoff": cutoff.isoformat(),
    }


async def sweep_approvals(session: AsyncSession, *, dry_run: bool) -> dict[str, Any]:
    """Drop decided approvals beyond the retention window.

    Only touches non-pending rows — pending approvals stay until they're
    decided or expire; long-pending ones surface in the dashboard instead.
    """
    days = settings.APPROVAL_RETENTION_DAYS
    if days <= 0:
        return {"skipped": True, "reason": "disabled"}
    cutoff = utcnow_naive() - timedelta(days=days)
    stmt = (
        select(Approval.id)
        .where(Approval.status != ApprovalStatus.PENDING)
        .where(Approval.created_at < cutoff)
    )
    ids = [r[0] for r in (await session.execute(stmt)).all()]
    if not dry_run and ids:
        await session.execute(delete(Approval).where(Approval.id.in_(ids)))
    return {
        "candidates": len(ids),
        "deleted": 0 if dry_run else len(ids),
        "cutoff": cutoff.isoformat(),
    }


async def sweep_audit_events(session: AsyncSession, *, dry_run: bool) -> dict[str, Any]:
    days = settings.AUDIT_RETENTION_DAYS
    if days <= 0:
        return {"skipped": True, "reason": "disabled"}
    cutoff = utcnow_naive() - timedelta(days=days)
    stmt = select(AuditEvent.id).where(AuditEvent.created_at < cutoff)
    ids = [r[0] for r in (await session.execute(stmt)).all()]
    if not dry_run and ids:
        await session.execute(delete(AuditEvent).where(AuditEvent.id.in_(ids)))
    return {
        "candidates": len(ids),
        "deleted": 0 if dry_run else len(ids),
        "cutoff": cutoff.isoformat(),
    }
