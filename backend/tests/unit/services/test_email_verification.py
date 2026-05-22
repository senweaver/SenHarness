"""Email-verification token issue / consume unit tests (M0.9)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.errors import Unauthorized
from app.core.security import utcnow_naive
from app.db.models.identity import IdentityStatus
from app.repositories.identity import IdentityRepository
from app.services import email_verification as svc

pytestmark = pytest.mark.asyncio


async def test_issue_then_consume_flips_identity_active(db_session, identity):
    await IdentityRepository(db_session).update(identity, status=IdentityStatus.PENDING)
    await db_session.flush()

    token = await svc.issue_token(db_session, identity_id=identity.id)
    assert isinstance(token, str)
    assert len(token) >= 32

    refreshed = await svc.consume_token(db_session, token=token)
    assert refreshed.id == identity.id
    assert refreshed.status == IdentityStatus.ACTIVE


async def test_expired_token_is_rejected(db_session, identity):
    await IdentityRepository(db_session).update(identity, status=IdentityStatus.PENDING)
    await db_session.flush()

    token = await svc.issue_token(db_session, identity_id=identity.id, ttl_seconds=60)
    row = await svc.latest_unconsumed_token(db_session, identity_id=identity.id)
    assert row is not None
    row.expires_at = utcnow_naive() - timedelta(seconds=10)
    await db_session.flush()

    with pytest.raises(Unauthorized):
        await svc.consume_token(db_session, token=token)


async def test_reused_token_is_rejected(db_session, identity):
    await IdentityRepository(db_session).update(identity, status=IdentityStatus.PENDING)
    await db_session.flush()

    token = await svc.issue_token(db_session, identity_id=identity.id)
    await svc.consume_token(db_session, token=token)
    await db_session.flush()

    with pytest.raises(Unauthorized):
        await svc.consume_token(db_session, token=token)


async def test_unknown_token_rejected(db_session):
    with pytest.raises(Unauthorized):
        await svc.consume_token(db_session, token="not-a-real-token")
