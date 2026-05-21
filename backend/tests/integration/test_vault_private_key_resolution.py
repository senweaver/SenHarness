"""Integration: SSH sandbox reads private keys via the workspace vault.

Three invariants the M2.5.10 spec calls out explicitly:

* ``vault://workspace/<key>`` resolves to the stored PEM string.
* Cross-workspace lookups never resolve — even when the key name
  matches, the lookup is keyed on ``(workspace_id, name)``.
* A missing key surfaces as the typed
  :class:`~app.services.vault.VaultKeyNotFoundError`, not an empty
  string (which would silently swap an "absent secret" for the empty
  PEM and let the connection fail much later).

The DB fixture is the standard ``db_session`` + ``workspace`` pair so
the cross-workspace assertion runs against real rows, not mocks.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import vault as vault_svc

# Synthetic PEM body (not a real key — the test never actually loads
# this into asyncssh). 200 chars keeps the row under any reasonable
# vault size cap.
_FAKE_PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZWQ\n"
    "yNTUxOQAAACBoT5pTfP8Yqv8mxSf+LJG7xAEXBzZxuKr3Vy3rZ3iU0AAAAJiQy0iEkMtIhA\n"
    "-----END OPENSSH PRIVATE KEY-----\n"
)


async def test_resolve_workspace_secret_round_trip(db_session, workspace, identity):
    await vault_svc.create_secret(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        name="ops_ed25519",
        plaintext=_FAKE_PEM,
    )
    await db_session.flush()

    pem = await vault_svc.reveal_workspace_secret(
        db_session, workspace_id=workspace.id, name="ops_ed25519"
    )
    assert pem == _FAKE_PEM


async def test_cross_workspace_isolation(db_session, workspace, identity):
    await vault_svc.create_secret(
        db_session,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        name="ops_ed25519",
        plaintext=_FAKE_PEM,
    )
    await db_session.flush()

    other_workspace = uuid.uuid4()
    with pytest.raises(vault_svc.VaultKeyNotFoundError) as exc:
        await vault_svc.reveal_workspace_secret(
            db_session, workspace_id=other_workspace, name="ops_ed25519"
        )
    assert exc.value.code == "vault.key_not_found"
    assert exc.value.key == "ops_ed25519"


async def test_missing_key_raises(db_session, workspace):
    with pytest.raises(vault_svc.VaultKeyNotFoundError) as exc:
        await vault_svc.reveal_workspace_secret(
            db_session, workspace_id=workspace.id, name="never_created"
        )
    assert exc.value.code == "vault.key_not_found"


async def test_template_substitution_still_works(db_session, workspace, identity):
    """Sanity: the new ``reveal_workspace_secret`` is the same lookup
    used by the ``${vault://workspace/...}`` template substituter.
    Ensures we didn't break the M0.6 path while exposing the helper.
    """
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
