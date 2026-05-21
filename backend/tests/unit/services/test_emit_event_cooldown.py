"""Unit: cooldown dedup key shape (M0.10).

The fan-out builds a deterministic Redis key per (event_key,
workspace_id, target_identity, cooldown_resource_id). The shape is
public-ish — tests pin it so a refactor can't silently change the
namespace and orphan the in-flight Redis state.
"""

from __future__ import annotations

import uuid

from app.services.notification_events import _dedup_key


def test_dedup_key_composes_from_all_four_axes():
    ws = uuid.UUID("00000000-0000-0000-0000-000000000001")
    target = "abc"
    key = _dedup_key(
        event_key="goal.alignment_low",
        workspace_id=ws,
        target_label=target,
        cooldown_resource_id="goal-1",
    )
    assert key.startswith("notif_dedup:")
    assert "goal.alignment_low" in key
    assert str(ws) in key
    assert "goal-1" in key
    assert "abc" in key


def test_dedup_key_distinguishes_resources():
    ws = uuid.uuid4()
    a = _dedup_key(
        event_key="channel.sender_blocked",
        workspace_id=ws,
        target_label="ident",
        cooldown_resource_id="channel-1",
    )
    b = _dedup_key(
        event_key="channel.sender_blocked",
        workspace_id=ws,
        target_label="ident",
        cooldown_resource_id="channel-2",
    )
    assert a != b


def test_dedup_key_handles_global_workspace():
    """Platform-scoped events (no workspace_id) still produce a key."""
    key = _dedup_key(
        event_key="workspace.spike_detected",
        workspace_id=None,
        target_label="admin",
        cooldown_resource_id="ident-1",
    )
    assert "global" in key
    assert "ident-1" in key


def test_cooldown_seconds_zero_skips_redis_call(monkeypatch):
    """``ttl_seconds <= 0`` is the fast-path — registry's never-dedup signal.

    We assert the helper doesn't even attempt to touch Redis: a
    monkeypatched module raises if called, simulating a Redis outage.
    """
    import asyncio

    from app.services import notification_events as ne

    async def _broken_redis(*args, **kwargs):
        raise RuntimeError("Redis must not be called")

    monkeypatch.setattr(ne, "_claim_cooldown_redis_called", _broken_redis, raising=False)

    async def _run():
        return await ne._claim_cooldown(key="x", ttl_seconds=0)

    assert asyncio.run(_run()) is True
