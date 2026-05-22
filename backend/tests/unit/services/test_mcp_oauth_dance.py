"""Unit: M2.5.4 OAuth dance — token caching + refresh + audit safety.

Mocks ``httpx.AsyncClient.post`` and a tiny in-memory vault so we
can exercise the three decision branches in
:func:`get_valid_token` without touching the real DB or IdP:

1. Cached envelope still valid → reuse, no HTTP.
2. Cached envelope expired with refresh_token → refresh flow.
3. No cache or refresh failed → fresh client_credentials dance.
"""

from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace

import pytest

from app.services import mcp_oauth as oauth_svc
from app.services.mcp_oauth import (
    McpOAuthConfig,
    McpOAuthUnauthorized,
    get_valid_token,
    perform_oauth_dance,
)

pytestmark = pytest.mark.asyncio


# ─── In-memory vault stub ───────────────────────────────────────
class _StubVault:
    """Replaces ``app.services.vault`` for the duration of a single test."""

    class _Item(SimpleNamespace):
        pass

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    class VaultKeyNotFoundError(LookupError):
        def __init__(self, key: str) -> None:
            super().__init__(f"vault key not found: {key!r}")
            self.code = "vault.key_not_found"
            self.key = key

    async def reveal_workspace_secret(self, db, *, workspace_id, name: str) -> str:
        if name not in self.store:
            raise self.VaultKeyNotFoundError(name)
        return self.store[name]

    async def _lookup_workspace_secret(self, db, *, workspace_id, name: str):
        if name not in self.store:
            return None
        return self._Item(name=name)

    async def replace_secret(self, db, *, item, plaintext: str):
        self.store[item.name] = plaintext

    async def create_secret(
        self,
        db,
        *,
        workspace_id,
        owner_identity_id,
        name: str,
        plaintext: str,
        kind=None,
        metadata=None,
    ):
        self.store[name] = plaintext


@pytest.fixture
def stub_vault(monkeypatch) -> _StubVault:
    vault = _StubVault()
    monkeypatch.setattr(oauth_svc, "vault_svc", vault)
    return vault


# ─── HTTP stub ──────────────────────────────────────────────────
class _StubHttpResponse:
    def __init__(self, status: int, payload: dict | str) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if isinstance(payload, dict) else payload

    def json(self) -> dict:
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")


class _StubAsyncClient:
    def __init__(self, *, responses: list[_StubHttpResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def __aenter__(self) -> _StubAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, *, data: dict, headers: dict) -> _StubHttpResponse:
        self.calls.append({"url": url, "data": data, "headers": headers})
        if not self._responses:
            raise AssertionError("no more stubbed responses")
        return self._responses.pop(0)


@pytest.fixture
def http_stub(monkeypatch):
    holder: dict[str, _StubAsyncClient] = {}

    def _factory(*responses: _StubHttpResponse):
        client = _StubAsyncClient(responses=list(responses))
        holder["client"] = client

        def _make(*_args, **_kwargs):
            return client

        monkeypatch.setattr(oauth_svc.httpx, "AsyncClient", _make)
        return client

    return _factory


def _config(**overrides) -> McpOAuthConfig:
    base = {
        "client_id": "cid",
        "client_secret": "csec",
        "token_url": "https://idp.example.com/token",
        "scopes": ["read:tools"],
        "refresh_grace_seconds": 60,
    }
    base.update(overrides)
    return McpOAuthConfig(**base)


# ─── Tests ──────────────────────────────────────────────────────
async def test_dance_happy_persists_envelope(stub_vault, http_stub):
    response = _StubHttpResponse(
        200,
        {
            "access_token": "atk-1",
            "refresh_token": "rtk-1",
            "expires_in": 3600,
            "token_type": "Bearer",
            "scope": "read:tools",
        },
    )
    http = http_stub(response)
    config = _config()
    server_id = uuid.uuid4()
    workspace_id = uuid.uuid4()

    result = await perform_oauth_dance(
        db=None,
        server_id=server_id,
        workspace_id=workspace_id,
        config=config,
    )

    assert result.access_token == "atk-1"
    assert not result.refreshed
    posted = http.calls[0]
    assert posted["url"] == "https://idp.example.com/token"
    assert posted["data"]["grant_type"] == "client_credentials"
    assert posted["data"]["client_id"] == "cid"
    assert posted["data"]["client_secret"] == "csec"
    assert posted["data"]["scope"] == "read:tools"
    cached = json.loads(stub_vault.store[config.vault_name(server_id)])
    assert cached["access_token"] == "atk-1"
    assert cached["refresh_token"] == "rtk-1"


async def test_dance_unauthorized_raises(stub_vault, http_stub):
    response = _StubHttpResponse(401, {"error": "invalid_client"})
    http_stub(response)
    config = _config()
    with pytest.raises(McpOAuthUnauthorized):
        await perform_oauth_dance(
            db=None,
            server_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            config=config,
        )


async def test_get_valid_token_reuses_cached(stub_vault, http_stub):
    server_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    config = _config()
    cached = {
        "access_token": "atk-cached",
        "refresh_token": "rtk-cached",
        "expires_at": time.time() + 3600,
        "token_type": "Bearer",
        "scope": "read:tools",
    }
    stub_vault.store[config.vault_name(server_id)] = json.dumps(cached)

    # No HTTP responses queued — if we hit the network the test fails.
    http_stub()

    result = await get_valid_token(
        db=None,
        server_id=server_id,
        workspace_id=workspace_id,
        config=config,
    )
    assert result.access_token == "atk-cached"
    assert not result.refreshed


async def test_get_valid_token_refreshes_when_expired(stub_vault, http_stub):
    server_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    config = _config()
    expired = {
        "access_token": "atk-old",
        "refresh_token": "rtk-old",
        "expires_at": time.time() - 60,
        "token_type": "Bearer",
        "scope": "read:tools",
    }
    stub_vault.store[config.vault_name(server_id)] = json.dumps(expired)

    refreshed = _StubHttpResponse(
        200,
        {
            "access_token": "atk-new",
            "refresh_token": "rtk-new",
            "expires_in": 1800,
        },
    )
    http = http_stub(refreshed)

    result = await get_valid_token(
        db=None,
        server_id=server_id,
        workspace_id=workspace_id,
        config=config,
    )
    assert result.access_token == "atk-new"
    assert result.refreshed
    posted = http.calls[0]
    assert posted["data"]["grant_type"] == "refresh_token"
    assert posted["data"]["refresh_token"] == "rtk-old"


async def test_get_valid_token_falls_back_to_dance_when_refresh_fails(stub_vault, http_stub):
    server_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    config = _config()
    expired = {
        "access_token": "atk-old",
        "refresh_token": "rtk-old",
        "expires_at": time.time() - 60,
        "token_type": "Bearer",
        "scope": None,
    }
    stub_vault.store[config.vault_name(server_id)] = json.dumps(expired)

    failed_refresh = _StubHttpResponse(400, {"error": "invalid_grant"})
    fresh_dance = _StubHttpResponse(
        200,
        {"access_token": "atk-fresh", "expires_in": 3600},
    )
    http = http_stub(failed_refresh, fresh_dance)

    result = await get_valid_token(
        db=None,
        server_id=server_id,
        workspace_id=workspace_id,
        config=config,
    )
    assert result.access_token == "atk-fresh"
    # First call was the refresh attempt, second was the fresh dance.
    assert http.calls[0]["data"]["grant_type"] == "refresh_token"
    assert http.calls[1]["data"]["grant_type"] == "client_credentials"


async def test_token_url_must_be_safe(stub_vault, http_stub):
    config = _config(token_url="http://169.254.169.254/token")
    http_stub()
    with pytest.raises(Exception) as excinfo:
        await perform_oauth_dance(
            db=None,
            server_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            config=config,
        )
    assert "metadata" in str(excinfo.value).lower() or "unsafe" in str(excinfo.value).lower()
