"""ARQ tasks for the notification pipeline (M0.10).

Two jobs:

* :func:`send_email_notification` is enqueued by
  :func:`app.services.notification_events.emit_event` whenever the
  effective channel set for a recipient includes
  :class:`NotificationChannel.EMAIL`. The job delegates to the
  configured :class:`EmailTransport`; on failure it raises so ARQ
  retries up to ``max_tries``. The matching ``on_job_end`` hook in
  :mod:`app.worker.arq_app` writes ``job.failed_permanent`` after
  three strikes, mirroring the M0.3 / M0.11 pattern.

* :func:`cleanup_old_notifications` runs daily as a cron and hard-
  deletes in-app rows older than the configured retention window.
  This is intentionally separate from the M0.11 retention sweep
  because notifications are not GDPR user-original-content (they
  carry summaries / metadata). Keeping the cron in this module also
  avoids cross-talk with the cascade watermarks.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete

from app.core.security import utcnow_naive
from app.db.models.notification import Notification
from app.db.session import get_session_factory
from app.services import audit as audit_svc
from app.services.email_transport import (
    EmailDispatchResult,
    get_email_transport,
)
from app.services.system_settings import (
    SystemSettingKey,
    get_system_setting,
)

log = logging.getLogger(__name__)


_DEFAULT_RETENTION_DAYS = 90


async def send_email_notification(ctx: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one notification email through the platform transport.

    The payload ships from :func:`emit_event` and carries enough info
    for the transport to render the message without a DB lookup. Any
    transport error is re-raised so ARQ retries; the worker hook
    promotes the third failure to ``job.failed_permanent``.
    """
    event_key = str(payload.get("event_key") or "unknown")
    to_email = str(payload.get("to_email") or "").strip()
    if not to_email:
        log.warning("send_email_notification: empty to_email for %s", event_key)
        return {"status": "skipped_no_address", "event_key": event_key}

    transport = get_email_transport()
    subject = str(payload.get("subject_fallback") or payload.get("title_key") or event_key)
    body_text = str(payload.get("body_fallback") or payload.get("message_key") or "")
    headers = {
        "X-SenHarness-Event-Key": event_key,
        "X-SenHarness-Idempotency-Key": str(payload.get("idempotency_key") or ""),
        "X-SenHarness-Urgency": str(payload.get("urgency") or "info"),
    }

    result: EmailDispatchResult = await transport.send(
        to=to_email,
        subject=subject,
        body_text=body_text,
        body_html=None,
        headers=headers,
    )
    if not result.ok:
        raise RuntimeError(f"email transport {result.transport} returned ok=False: {result.error}")
    return {
        "status": "sent",
        "event_key": event_key,
        "transport": result.transport,
        "message_id": result.message_id,
    }


async def cleanup_old_notifications(
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """Daily cron: purge in-app notifications past the retention window.

    Reads :data:`SystemSettingKey.NOTIFICATION_DEFAULTS` for
    ``in_app_retention_days``. Workspaces can override per tenant via
    ``home_config_json["notifications"]["retention_days"]`` in a future
    milestone — for M0.10 the platform default is the source of truth.
    """
    factory = get_session_factory()
    async with factory() as db:
        defaults = await get_system_setting(db, SystemSettingKey.NOTIFICATION_DEFAULTS, default={})
        if not isinstance(defaults, dict):
            defaults = {}
        retention_days = int(defaults.get("in_app_retention_days") or _DEFAULT_RETENTION_DAYS)
        cutoff = utcnow_naive() - timedelta(days=retention_days)
        stmt = delete(Notification).where(Notification.created_at < cutoff)
        result = await db.execute(stmt)
        deleted = int(getattr(result, "rowcount", 0) or 0)
        await audit_svc.record(
            db,
            action="notification.cleanup_swept",
            actor_identity_id=None,
            workspace_id=None,
            resource_type="notification",
            resource_id=None,
            summary=(
                f"cleanup_old_notifications removed {deleted} rows older than {retention_days}d"
            ),
            metadata={
                "deleted": deleted,
                "retention_days": retention_days,
            },
        )
        await db.commit()
    return {"status": "swept", "deleted": deleted, "retention_days": retention_days}


async def on_notification_job_failed_permanent(ctx: dict[str, Any], exc: BaseException) -> None:
    """ARQ permanent-failure recorder for the notification email job.

    Mirrors :func:`app.jobs.judge.on_job_failed_permanent` so the
    worker's central ``on_job_end`` dispatch table can route email
    failures here without growing a switch statement.
    """
    try:
        function_name = str(ctx.get("function") or "unknown")
        args = ctx.get("args") or []
        event_key = "unknown"
        if args and isinstance(args[0], dict):
            event_key = str(args[0].get("event_key") or "unknown")

        async with get_session_factory()() as db:
            await audit_svc.record(
                db,
                action="job.failed_permanent",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="job",
                resource_id=None,
                summary=(f"job {function_name} (event {event_key}) failed permanently: {exc!r}"),
                metadata={
                    "function": function_name,
                    "event_key": event_key,
                    "exception": repr(exc)[:500],
                },
            )
            # Avoid recursive emit on the email task itself: that would
            # re-enqueue another email, which has just been declared
            # permanently failing. The audit row is enough; admins
            # already get IN_APP via the matching ``job.failed_permanent``
            # emitted by the upstream task that triggered the email.
            await db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("notification on_job_failed_permanent crashed")


_ = uuid  # reserved for future per-event metadata derivation

__all__ = [
    "cleanup_old_notifications",
    "on_notification_job_failed_permanent",
    "send_email_notification",
]
