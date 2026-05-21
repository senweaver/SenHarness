"""DB-backed coverage for ``resolve_vault_template``.

The substitution itself is pure but it has to walk the DB to find a
``VaultItem`` by ``(workspace_id, name)``; we use the standard
``db_session`` + ``workspace`` fixtures so the cross-workspace
guarantee is tested against real rows rather than a mock.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import vault as vault_svc


async def test_passthrough_when_no_template(db_session, workspace):
    out = await vault_svc.resolve_vault_template(
        db_session, workspace_id=workspace.id, template="plain header value"
    )
    assert out == "plain header value"


async def test_resolves_workspace_secret(db_session, workspace, identity):
    await vault_svc.create_secret(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        name="api_key",
        plaintext="sk-test-1234",
    )
    await db_session.flush()

    out = await vault_svc.resolve_vault_template(
        db_session,
        workspace_id=workspace.id,
        template="Bearer ${vault://workspace/api_key}",
    )
    assert out == "Bearer sk-test-1234"


async def test_multiple_templates_resolved(db_session, workspace, identity):
    await vault_svc.create_secret(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        name="user",
        plaintext="alice",
    )
    await vault_svc.create_secret(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        name="pw",
        plaintext="hunter2",
    )
    await db_session.flush()

    out = await vault_svc.resolve_vault_template(
        db_session,
        workspace_id=workspace.id,
        template="${vault://workspace/user}:${vault://workspace/pw}",
    )
    assert out == "alice:hunter2"


async def test_missing_key_raises(db_session, workspace):
    with pytest.raises(vault_svc.VaultKeyNotFoundError) as exc:
        await vault_svc.resolve_vault_template(
            db_session,
            workspace_id=workspace.id,
            template="${vault://workspace/missing}",
        )
    assert exc.value.code == "vault.key_not_found"
    assert exc.value.key == "missing"


async def test_cross_workspace_isolated(db_session, workspace, identity):
    await vault_svc.create_secret(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        name="other_key",
        plaintext="secret-of-ws-A",
    )
    await db_session.flush()

    other_ws = uuid.uuid4()
    with pytest.raises(vault_svc.VaultKeyNotFoundError):
        await vault_svc.resolve_vault_template(
            db_session,
            workspace_id=other_ws,
            template="${vault://workspace/other_key}",
        )


async def test_non_workspace_scope_rejected(db_session, workspace):
    with pytest.raises(ValueError) as exc:
        await vault_svc.resolve_vault_template(
            db_session,
            workspace_id=workspace.id,
            template="${vault://platform/master_key}",
        )
    assert "platform" in str(exc.value)
