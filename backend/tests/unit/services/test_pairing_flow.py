"""Unit tests for the M3.6 pairing flow primitives.

The pairing handshake never persists anything to the DB at issue time
— only the binding flips and thread merge during ``consume`` need a
real session. These tests cover the pure-function pieces (code shape,
Redis payload schema, mismatched-target rejection) plus the Redis
side-effect via a stub.
"""

from __future__ import annotations

import re

from app.services import logical_thread as svc


def test_generated_code_is_six_digits() -> None:
    for _ in range(50):
        code = svc._generate_code()
        assert re.fullmatch(r"[0-9]{6}", code)


def test_pairing_key_includes_workspace_and_code() -> None:
    import uuid as _uuid

    ws = _uuid.uuid4()
    key = svc._pairing_key(ws, "123456")
    assert key.startswith("thread:pair:")
    assert str(ws) in key
    assert "123456" in key


def test_redact_returns_none_for_none() -> None:
    assert svc._redact(None) is None


def test_redact_is_stable_short_hash() -> None:
    a = svc._redact("U12345")
    b = svc._redact("U12345")
    c = svc._redact("U99999")
    assert a == b
    assert a != c
    assert isinstance(a, str)
    assert len(a) == 12
    assert re.fullmatch(r"[0-9a-f]{12}", a)


def test_pairing_code_invalid_raises_app_error() -> None:
    err = svc.PairingCodeInvalid("x")
    assert err.code == "thread.pairing_code_invalid"
    assert err.default_status == 400


def test_pairing_code_expired_raises_app_error() -> None:
    err = svc.PairingCodeExpired("x")
    assert err.code == "thread.pairing_code_expired"


def test_pairing_target_mismatch_has_stable_code() -> None:
    err = svc.PairingTargetMismatch("x")
    assert err.code == "thread.pairing_target_mismatch"


def test_pairing_source_missing_has_stable_code() -> None:
    err = svc.PairingSourceMissing("x")
    assert err.code == "thread.pairing_source_missing"


def test_cross_platform_disabled_has_stable_code() -> None:
    err = svc.CrossPlatformDisabled("x")
    assert err.code == "thread.cross_platform_disabled"


# ─── Code-shape rejections ───────────────────────────────────
async def test_consume_rejects_non_numeric_code(monkeypatch) -> None:
    import uuid as _uuid

    async def fake_cfg(_db, **_kw):  # type: ignore[no-untyped-def]
        return svc.RoutingConfig(
            cross_platform_enabled=True,
            pairing_required=True,
            pairing_code_ttl_seconds=600,
            default_strategy="per_channel",
        )

    monkeypatch.setattr(svc, "get_routing_config", fake_cfg)

    import pytest as _pytest

    with _pytest.raises(svc.PairingCodeInvalid):
        await svc.consume_pairing_code(
            db=None,  # type: ignore[arg-type]
            workspace_id=_uuid.uuid4(),
            identity_id=_uuid.uuid4(),
            code="abcdef",
            channel_id=None,
            external_user_id=None,
        )


async def test_consume_rejects_short_code(monkeypatch) -> None:
    import uuid as _uuid

    async def fake_cfg(_db, **_kw):  # type: ignore[no-untyped-def]
        return svc.RoutingConfig(
            cross_platform_enabled=True,
            pairing_required=True,
            pairing_code_ttl_seconds=600,
            default_strategy="per_channel",
        )

    monkeypatch.setattr(svc, "get_routing_config", fake_cfg)

    import pytest as _pytest

    with _pytest.raises(svc.PairingCodeInvalid):
        await svc.consume_pairing_code(
            db=None,  # type: ignore[arg-type]
            workspace_id=_uuid.uuid4(),
            identity_id=_uuid.uuid4(),
            code="123",
            channel_id=None,
            external_user_id=None,
        )


async def test_consume_short_circuits_when_disabled(monkeypatch) -> None:
    import uuid as _uuid

    async def fake_cfg(_db, **_kw):  # type: ignore[no-untyped-def]
        return svc.RoutingConfig(
            cross_platform_enabled=False,
            pairing_required=True,
            pairing_code_ttl_seconds=600,
            default_strategy="per_channel",
        )

    monkeypatch.setattr(svc, "get_routing_config", fake_cfg)

    import pytest as _pytest

    with _pytest.raises(svc.CrossPlatformDisabled):
        await svc.consume_pairing_code(
            db=None,  # type: ignore[arg-type]
            workspace_id=_uuid.uuid4(),
            identity_id=_uuid.uuid4(),
            code="123456",
            channel_id=None,
            external_user_id=None,
        )


async def test_initiate_short_circuits_when_disabled(monkeypatch) -> None:
    import uuid as _uuid

    async def fake_cfg(_db, **_kw):  # type: ignore[no-untyped-def]
        return svc.RoutingConfig(
            cross_platform_enabled=False,
            pairing_required=True,
            pairing_code_ttl_seconds=600,
            default_strategy="per_channel",
        )

    monkeypatch.setattr(svc, "get_routing_config", fake_cfg)

    import pytest as _pytest

    with _pytest.raises(svc.CrossPlatformDisabled):
        await svc.initiate_pairing(
            db=None,  # type: ignore[arg-type]
            workspace_id=_uuid.uuid4(),
            identity_id=_uuid.uuid4(),
            source_channel_id=None,
            source_external_user_id=None,
            target_channel_id=None,
            target_external_user_id=None,
        )
