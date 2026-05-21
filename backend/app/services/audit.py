"""Audit service — single choke point for recording platform events.

All write paths that matter for auditors should call :func:`record` with a
stable ``action`` string. The function is intentionally forgiving: if the DB
is unavailable, it logs a warning and swallows the error so we never break a
user-facing write because an audit row couldn't land.

Recommended action namespaces:

* ``auth.login``, ``auth.login_failed``, ``auth.logout``, ``auth.refresh``
* ``agent.create``, ``agent.update``, ``agent.delete``, ``agent.clone``
* ``agent.visibility_change`` (metadata: ``{from, to}``)
* ``agent.report``, ``report.decide`` (resource_type=report)
* ``squad.create``, ``squad.update``, ``squad.delete``
* ``approval.decide`` (metadata: ``{approved, reason, tool_name}``)
* ``session.create``, ``session.share``
* ``workspace.invite.create``, ``workspace.invite.accept``
* ``marketplace.clone``
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.audit import AuditRepository

log = logging.getLogger(__name__)


def _client_info(request: Request | None) -> tuple[str | None, str | None]:
    if request is None:
        return None, None
    ip: str | None = None
    try:
        # Respect X-Forwarded-For when behind a reverse proxy.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            ip = xff.split(",")[0].strip()
        elif request.client is not None:
            ip = request.client.host
    except Exception:  # pragma: no cover
        pass
    ua = request.headers.get("user-agent")
    return ip, ua


async def record(
    session: AsyncSession,
    *,
    action: str,
    actor_identity_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    summary: str | None = None,
    metadata: dict[str, Any] | None = None,
    request: Request | None = None,
) -> None:
    """Insert an audit row. Never raises."""
    try:
        ip, ua = _client_info(request)
        await AuditRepository(session).add(
            workspace_id=workspace_id,
            actor_identity_id=actor_identity_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            summary=summary,
            metadata_json=metadata or {},
            ip_address=ip,
            user_agent=ua,
        )
    except Exception as e:  # pragma: no cover
        # Don't let audit failures break the business transaction. Just log.
        log.warning("audit.record failed for %s: %s", action, e)
