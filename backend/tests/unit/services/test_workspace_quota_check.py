"""Unit: ``workspace_quota.check_can_create`` failure modes (M0.12).

Each case verifies the typed exception + stable ``code`` so the route
layer + frontend i18n keep working without integration setup.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import (
    CreationNotPermitted,
    CreationRateLimited,
    QuotaExceeded,
)
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


async def test_self_register_creation_blocked_by_default(db_session, identity):
    """Self-registered identities cannot create beyond their personal one."""
    with pytest.raises(CreationNotPermitted) as ei:
        await quota_svc.check_can_create(
            db_session,
            identity_id=identity.id,
            creation_kind=CreationKind.MANUAL,
        )
    assert ei.value.code == "workspace.creation_not_permitted"


async def test_quota_exceeded_returns_typed_error(db_session, identity):
    """Once the personal slot is filled, MANUAL creation 403s."""
    repo = IdentityRepository(db_session)
    # Bump the override so the kind gate passes; we want to isolate the
    # limit branch.
    await repo.update(identity, workspace_quota_override=1)
    await db_session.flush()

    # Fill the slot.
    await workspace_svc.create_workspace(
        db_session,
        name="Sole",
        slug=f"sole-{uuid.uuid4().hex[:6]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    # Override gating: make creation_kind_allowed True via OAuth.
    ident = await repo.get(identity.id)
    await repo.update(ident, oauth_provider="github", oauth_id=f"gh-{uuid.uuid4().hex[:8]}")
    await db_session.flush()

    with pytest.raises(QuotaExceeded) as ei:
        await quota_svc.check_can_create(
            db_session,
            identity_id=identity.id,
            creation_kind=CreationKind.MANUAL,
        )
    assert ei.value.code == "workspace.quota_exceeded"


async def test_rate_limit_trips_at_third_attempt(db_session, identity):
    """Two attempts allowed in window; third trips ``creation_rate_limit``."""
    repo = IdentityRepository(db_session)
    await repo.update(
        identity,
        oauth_provider="github",
        oauth_id=f"gh-{uuid.uuid4().hex[:8]}",
        workspace_quota_override=10,
    )
    await db_session.flush()

    # First two pass.
    for _ in range(2):
        await quota_svc.check_can_create(
            db_session,
            identity_id=identity.id,
            creation_kind=CreationKind.MANUAL,
        )

    with pytest.raises(CreationRateLimited) as ei:
        await quota_svc.check_can_create(
            db_session,
            identity_id=identity.id,
            creation_kind=CreationKind.MANUAL,
        )
    assert ei.value.code == "workspace.creation_rate_limit"


async def test_admin_provision_bypasses_self_register_gate(db_session, identity):
    """``ADMIN_PROVISION`` ignores the self-register opt-out toggle."""
    # Self-register identity (default), admin provisioning should still pass.
    repo = IdentityRepository(db_session)
    await repo.update(identity, workspace_quota_override=5)
    await db_session.flush()

    # No raise.
    snapshot = await quota_svc.check_can_create(
        db_session,
        identity_id=identity.id,
        creation_kind=CreationKind.ADMIN_PROVISION,
    )
    assert snapshot.remaining > 0
