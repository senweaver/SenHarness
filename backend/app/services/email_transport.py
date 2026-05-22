"""Email transport interface for the notification fan-out.

The notification job calls :func:`get_email_transport` once per outbound
email and treats the returned object as a small async-only contract.
We keep the protocol tiny so additional adapters (Mailgun / Postmark /
SES) only have to implement :meth:`EmailTransport.send` and update
:func:`reload_email_transport_from_settings`.

M0.10 shipped only :class:`LogEmailTransport` (audit + log, no real
mail). M0.13 adds :class:`SmtpEmailTransport` plus the runtime
swap-in :func:`reload_email_transport_from_settings` which reads the
``email.smtp`` platform-settings section and replaces the process-wide
singleton without a restart.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session_factory
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


@dataclass(slots=True)
class EmailDispatchResult:
    """Outcome of a single outbound email attempt.

    ``transport`` is the kind string that selected the adapter — useful
    for audit forensics when the platform later runs more than one in
    parallel (e.g. SMTP primary + Mailgun fallback).
    """

    ok: bool
    transport: str
    message_id: str | None
    error: str | None = None


@runtime_checkable
class EmailTransport(Protocol):
    """Async contract every email backend must satisfy."""

    transport_kind: str

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> EmailDispatchResult: ...


class LogEmailTransport:
    """No-op transport used when no SMTP backend is configured.

    Writes a single ``email.dispatched_via_log`` audit row keyed on the
    SHA-256 hash of the recipient address (so the audit feed cannot
    leak the raw email when screen-shared) plus a console log line at
    INFO. Always returns ``ok=True`` because it cannot fail — the
    notification job's three-strikes logic only kicks in when the
    transport raises or returns ``ok=False``.
    """

    transport_kind: str = "log"

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> EmailDispatchResult:
        to_hash = hashlib.sha256(to.encode("utf-8")).hexdigest()[:16]
        log.info(
            "email.log_transport: to_hash=%s subject=%r body_chars=%d",
            to_hash,
            subject,
            len(body_text or ""),
        )
        try:
            factory = get_session_factory()
            async with factory() as db:
                await audit_svc.record(
                    db,
                    action="email.dispatched_via_log",
                    actor_identity_id=None,
                    workspace_id=None,
                    resource_type="email",
                    resource_id=None,
                    summary=f"log email to_hash={to_hash}",
                    metadata={
                        "to_hash": to_hash,
                        "subject": subject[:200],
                        "headers_keys": sorted((headers or {}).keys()),
                        "has_html": body_html is not None,
                    },
                )
                await db.commit()
        except Exception as exc:  # pragma: no cover - audit best-effort
            log.warning("LogEmailTransport audit failed: %s", exc)
        return EmailDispatchResult(ok=True, transport=self.transport_kind, message_id=None)


class SmtpEmailTransport:
    """Real SMTP transport used when ``email.smtp.enabled = True``.

    Uses ``aiosmtplib`` (declared as the ``[email]`` optional extra in
    ``pyproject.toml``) to keep the cold-import surface small for
    deployments that never enable SMTP. Falls back to a clear error
    instead of silently swallowing connection failures so the
    notification job's three-strikes logic + audit kicks in.

    ``password`` is the resolved plaintext (callers fetch it from the
    vault before constructing the transport); the constructor never
    looks at vault references.
    """

    transport_kind: str = "smtp"

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        from_address: str,
        use_tls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_address = from_address
        self.use_tls = use_tls

    async def send(
        self,
        *,
        to: str,
        subject: str,
        body_text: str,
        body_html: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> EmailDispatchResult:
        try:
            from email.message import EmailMessage

            import aiosmtplib
        except ImportError as exc:
            log.error(
                "SmtpEmailTransport requires the [email] optional extra; "
                "install with `pip install .[email]`"
            )
            return EmailDispatchResult(
                ok=False,
                transport=self.transport_kind,
                message_id=None,
                error=f"missing aiosmtplib: {exc}",
            )

        message = EmailMessage()
        message["From"] = self.from_address
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body_text)
        if body_html:
            message.add_alternative(body_html, subtype="html")
        for k, v in (headers or {}).items():
            message[k] = v

        try:
            await aiosmtplib.send(
                message,
                hostname=self.host,
                port=self.port,
                username=self.username or None,
                password=self.password or None,
                start_tls=self.use_tls,
            )
        except Exception as exc:
            log.warning(
                "SmtpEmailTransport send failed for host=%s port=%s: %s",
                self.host,
                self.port,
                exc,
            )
            return EmailDispatchResult(
                ok=False,
                transport=self.transport_kind,
                message_id=None,
                error=f"smtp_send_failed: {exc}",
            )
        message_id = message.get("Message-Id")
        return EmailDispatchResult(ok=True, transport=self.transport_kind, message_id=message_id)


_DEFAULT_TRANSPORT: EmailTransport = LogEmailTransport()


def get_email_transport() -> EmailTransport:
    """Return the platform-configured transport singleton.

    The instance is replaced atomically by
    :func:`set_email_transport` (test hook) and
    :func:`reload_email_transport_from_settings` (M0.13 startup +
    Redis invalidation handler).
    """
    return _DEFAULT_TRANSPORT


def set_email_transport(transport: EmailTransport) -> None:
    """Swap the singleton. Production callers should go through
    :func:`reload_email_transport_from_settings` so the platform
    settings stay the source of truth.
    """
    global _DEFAULT_TRANSPORT
    _DEFAULT_TRANSPORT = transport


async def _resolve_smtp_password(db: AsyncSession, *, password_ref: str | None) -> str | None:
    """Fetch the SMTP password from the platform vault.

    The vault lives per-workspace in :mod:`app.services.vault`; SMTP
    settings are platform-wide and need a different lookup. For M0.13
    we resolve via a synthetic "platform" vault scope: the operator
    creates a vault entry in the platform workspace (defined as the
    first workspace owned by a platform admin) and references its
    name. When the reference is unset or the vault entry can't be
    read we return ``None`` so the SMTP transport can decide whether
    to attempt anonymous auth.

    We look up by name across the global VaultItem table because the
    M0.13 admin UI doesn't yet expose a workspace selector; M3 SSO
    will harden this once dedicated platform credentials land.
    """
    if not password_ref:
        return None
    try:
        from sqlalchemy import select

        from app.db.models.vault import VaultItem
        from app.services import vault as vault_svc

        stmt = (
            select(VaultItem)
            .where(VaultItem.name == password_ref)
            .where(VaultItem.deleted_at.is_(None))
            .order_by(VaultItem.created_at.desc())
            .limit(1)
        )
        item = (await db.execute(stmt)).scalar_one_or_none()
        if item is None:
            log.warning("platform SMTP password ref %r not found in vault", password_ref)
            return None
        return await vault_svc.reveal_secret(item)
    except Exception as exc:  # pragma: no cover - vault best-effort
        log.warning("platform SMTP password lookup failed: %s", exc)
        return None


async def reload_email_transport_from_settings(db: AsyncSession) -> str:
    """Read the ``email.smtp`` section and swap the singleton accordingly.

    Returns the kind string of the resolved transport so the caller
    can log / surface it. Falls back to ``LogEmailTransport`` when
    SMTP is disabled or required fields are missing — never raises.
    """
    try:
        # Late import to break the cycle:
        # platform_settings → email_transport → platform_settings.
        from app.services.platform_settings import (
            PlatformSettingsSection,
            get_section,
        )

        section_value = await get_section(db, section=PlatformSettingsSection.EMAIL_SMTP)
        smtp = section_value
    except Exception as exc:
        log.warning("email transport reload: failed to read settings (%s)", exc)
        set_email_transport(LogEmailTransport())
        return "log"

    enabled = bool(getattr(smtp, "enabled", False))
    host = getattr(smtp, "host", None)
    from_address = getattr(smtp, "from_address", None)
    if not enabled or not host or not from_address:
        set_email_transport(LogEmailTransport())
        return "log"

    password = await _resolve_smtp_password(db, password_ref=getattr(smtp, "password_ref", None))
    transport = SmtpEmailTransport(
        host=host,
        port=int(getattr(smtp, "port", 587)),
        username=getattr(smtp, "username", None),
        password=password,
        from_address=str(from_address),
        use_tls=bool(getattr(smtp, "use_tls", True)),
    )
    set_email_transport(transport)
    return "smtp"


__all__ = [
    "EmailDispatchResult",
    "EmailTransport",
    "LogEmailTransport",
    "SmtpEmailTransport",
    "get_email_transport",
    "reload_email_transport_from_settings",
    "set_email_transport",
]
