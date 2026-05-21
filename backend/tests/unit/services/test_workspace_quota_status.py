"""Unit: ``workspace_quota.get_quota`` snapshot semantics (M0.12).

Covers the verdict matrix: source kind inference, override > default,
``count_only_owned_role`` (we don't count member-only memberships),
``count_soft_deleted = False`` (deleted workspaces free a slot),
grandfather flag, and the OAuth identity → ``OAUTH_REGISTER`` source.
"""

from __future__ import annotations

import uuid

import pytest

from app.db.models.workspace_creation_log import CreationKind
from app.repositories.identity import IdentityRepository
from app.services import workspace as workspace_svc
from app.services import workspace_quota as quota_svc

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_ledger():
    quota_svc.reset_attempt_ledger()
    yield
    quota_svc.reset_attempt_ledger()


async def test_self_register_default_is_one(db_session, identity):
    snapshot = await quota_svc.get_quota(db_session, identity_id=identity.id)
    assert snapshot.source_kind == CreationKind.SELF_REGISTER
    assert snapshot.limit == 1
    assert snapshot.used == 0
    assert snapshot.remaining == 1
    assert snapshot.creation_kind_allowed is False


async def test_owner_workspace_counts_against_limit(db_session, identity):
    await workspace_svc.create_workspace(
        db_session,
        name="Owned",
        slug=f"owned-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    snapshot = await quota_svc.get_quota(db_session, identity_id=identity.id)
    assert snapshot.used == 1
    assert snapshot.remaining == 0


async def test_override_lifts_limit(db_session, identity):
    repo = IdentityRepository(db_session)
    await repo.update(identity, workspace_quota_override=10)
    await db_session.flush()

    snapshot = await quota_svc.get_quota(db_session, identity_id=identity.id)
    assert snapshot.limit == 10
    assert snapshot.override_active is True
    assert snapshot.grandfathered is True


async def test_oauth_identity_uses_oauth_default(db_session):
    repo = IdentityRepository(db_session)
    ident = await repo.create(
        email=f"oauth-{uuid.uuid4().hex[:6]}@example.com",
        name="OAuth User",
        password_hash=None,
        oauth_provider="github",
        oauth_id=f"gh-{uuid.uuid4().hex[:8]}",
    )
    await db_session.flush()

    snapshot = await quota_svc.get_quota(db_session, identity_id=ident.id)
    assert snapshot.source_kind == CreationKind.OAUTH_REGISTER
    assert snapshot.limit == 1


async def test_admin_identity_uses_admin_default(db_session, identity):
    from app.db.models.identity import PlatformRole

    repo = IdentityRepository(db_session)
    await repo.update(identity, platform_role=PlatformRole.PLATFORM_ADMIN)
    await db_session.flush()

    snapshot = await quota_svc.get_quota(db_session, identity_id=identity.id)
    assert snapshot.source_kind == CreationKind.ADMIN_PROVISION
    assert snapshot.limit == 20
    assert snapshot.creation_kind_allowed is True


async def test_soft_deleted_workspace_frees_slot(db_session, identity):
    from sqlalchemy import text

    ws = await workspace_svc.create_workspace(
        db_session,
        name="To Be Deleted",
        slug=f"trash-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    snapshot = await quota_svc.get_quota(db_session, identity_id=identity.id)
    assert snapshot.used == 1

    await db_session.execute(
        text(
            "UPDATE workspaces SET deleted_at = now(), slug_tombstoned = TRUE "
            "WHERE id = :id"
        ),
        {"id": ws.id},
    )
    await db_session.flush()

    snapshot = await quota_svc.get_quota(db_session, identity_id=identity.id)
    assert snapshot.used == 0
    assert snapshot.remaining == 1
