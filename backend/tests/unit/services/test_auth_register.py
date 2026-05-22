"""Service-layer tests for auth.register registration mode handling (M0.9)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.errors import InvitationRequired, RegistrationClosed
from app.db.models.email_verification import EmailVerificationToken
from app.db.models.identity import IdentityStatus
from app.services import auth as svc
from app.services import workspace as workspace_svc
from app.services.auth import RegistrationMode
from app.services.system_settings import (
    SystemSettingKey,
    set_system_setting,
)

pytestmark = pytest.mark.asyncio


def _email() -> str:
    return f"reg-{uuid.uuid4().hex[:8]}@example.com"


async def _set_mode(db, mode: RegistrationMode) -> None:
    await set_system_setting(db, SystemSettingKey.REGISTRATION_MODE, mode.value)
    await db.flush()


async def _set_verify(db, *, on: bool) -> None:
    await set_system_setting(db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, on)
    await db.flush()


async def test_open_personal_default_provisions_workspace_and_tokens(db_session):
    await _set_mode(db_session, RegistrationMode.OPEN_PERSONAL)
    await _set_verify(db_session, on=False)

    result = await svc.register(
        db_session,
        email=_email(),
        name="Personal Pat",
        password="correct horse battery staple",
    )

    assert result.identity.status == IdentityStatus.ACTIVE
    assert result.workspace is not None
    assert result.workspace.slug.startswith("reg-")
    assert result.auto_login_tokens is not None
    assert result.auto_login_tokens.access_token
    assert result.auto_login_tokens.refresh_token
    assert result.requires_email_verification is False
    assert result.registration_mode == RegistrationMode.OPEN_PERSONAL
    assert result.verification_token is None


async def test_invite_only_without_code_raises(db_session):
    await _set_mode(db_session, RegistrationMode.OPEN_INVITE_ONLY)
    await _set_verify(db_session, on=False)

    with pytest.raises(InvitationRequired):
        await svc.register(
            db_session,
            email=_email(),
            name="Iva Invite",
            password="correct horse battery staple",
        )


async def test_invite_only_with_code_joins_existing_workspace(db_session, workspace, identity):
    await _set_mode(db_session, RegistrationMode.OPEN_INVITE_ONLY)
    await _set_verify(db_session, on=False)

    invite = await workspace_svc.create_invitation(
        db_session,
        workspace_id=workspace.id,
        invited_by=identity.id,
        email=None,
    )
    await db_session.flush()

    result = await svc.register(
        db_session,
        email=_email(),
        name="Carl Coder",
        password="correct horse battery staple",
        invitation_code=invite.code,
    )

    assert result.workspace is not None
    assert result.workspace.id == workspace.id
    assert result.workspace_slug_warning is False
    # Invite-only mode never auto-logs the user in.
    assert result.auto_login_tokens is None


async def test_closed_mode_rejects_register(db_session):
    await _set_mode(db_session, RegistrationMode.CLOSED)

    with pytest.raises(RegistrationClosed):
        await svc.register(
            db_session,
            email=_email(),
            name="Nope Person",
            password="correct horse battery staple",
        )


async def test_email_verification_gate_marks_pending_and_issues_token(db_session):
    await _set_mode(db_session, RegistrationMode.OPEN_PERSONAL)
    await _set_verify(db_session, on=True)

    result = await svc.register(
        db_session,
        email=_email(),
        name="Verify Vivian",
        password="correct horse battery staple",
    )
    assert result.identity.status == IdentityStatus.PENDING
    assert result.requires_email_verification is True
    assert result.auto_login_tokens is None
    assert result.verification_token is not None

    rows = (
        (
            await db_session.execute(
                select(EmailVerificationToken).where(
                    EmailVerificationToken.identity_id == result.identity.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].consumed_at is None


async def test_audit_rows_emitted(db_session):
    from app.db.models.audit import AuditEvent

    await _set_mode(db_session, RegistrationMode.OPEN_PERSONAL)
    await _set_verify(db_session, on=False)

    result = await svc.register(
        db_session,
        email=_email(),
        name="Audrey Audit",
        password="correct horse battery staple",
    )
    await db_session.flush()

    actions = (
        (
            await db_session.execute(
                select(AuditEvent.action).where(AuditEvent.actor_identity_id == result.identity.id)
            )
        )
        .scalars()
        .all()
    )
    assert "auth.registered" in actions
    assert "auth.workspace_provisioned" in actions
