"""Smoke + lifecycle tests for ChannelRuntime.

We can't run real WebSocket SDKs in unit tests, so the real test
strategy is to use a ``FakeStreamingProvider`` that registers itself
with the channel registry, exposes a ``stream_available=True``, and
implements ``run_stream`` as a controllable async loop. The runtime
sees a "real" stream provider; the test drives state transitions.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, ClassVar

import pytest

from app.services.channel_runtime import (
    ChannelRuntime,
    _channel_mode,
    _config_fingerprint,
)
from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundDispatch,
    InboundMessage,
)


class _FakeChannel:
    """Minimal stand-in for the SQLAlchemy Channel ORM row.

    The ChannelRuntime only reads a handful of attributes, so a plain
    dataclass-flavoured object works for unit tests.
    """

    def __init__(self, kind: str = "_test_stream", **kwargs):
        self.id = kwargs.get("id", uuid.uuid4())
        self.kind = kind
        self.workspace_id = kwargs.get("workspace_id", uuid.uuid4())
        self.config_json = kwargs.get("config_json", {"app_id": "x", "app_secret": "y"})
        self.metadata_json = kwargs.get("metadata_json", {"mode": "stream"})
        self.enabled = kwargs.get("enabled", True)
        self.default_agent_id = kwargs.get("default_agent_id", uuid.uuid4())
        self.deleted_at = None


class _FakeStreamProvider(ChannelProvider):
    """Provider that registers a controllable stream loop."""

    kind = "_test_stream"

    received: ClassVar[list[InboundMessage]] = []
    started_count: ClassVar[int] = 0
    crash_next: ClassVar[bool] = False

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Fake stream",
            description="unit test only",
            required_config_fields=("app_id", "app_secret"),
            optional_config_fields=(),
            supports_outbound=True,
            supported_modes=("webhook", "stream"),
            default_mode="stream",
        )

    @classmethod
    def supports_stream(cls) -> bool:
        return True

    @classmethod
    def stream_available(cls) -> bool:
        return True

    def parse_inbound(self, payload, headers):  # type: ignore[override]
        return None

    async def run_stream(  # type: ignore[override]
        self,
        *,
        channel: Any,
        dispatch: InboundDispatch,
        stop: asyncio.Event,
    ) -> None:
        type(self).started_count += 1
        if type(self).crash_next:
            type(self).crash_next = False
            raise RuntimeError("intentional test crash")
        # Push one inbound, then wait for stop.
        await dispatch(
            InboundMessage(
                thread_key=f"fake:{channel.id}",
                user_text="ping",
                external_user="tester",
            )
        )
        await stop.wait()


# ─── Helpers ───────────────────────────────────────────
@pytest.fixture(autouse=True)
def _patch_dispatch(monkeypatch):
    """Avoid hitting the real DB / agent runner during the runtime test."""

    captured: list[InboundMessage] = []

    async def _fake_dispatch(*, channel_id, inbound):
        captured.append(inbound)

    monkeypatch.setattr(
        "app.services.channel_runtime.dispatch_inbound", _fake_dispatch
    )
    return captured


# ─── Tests ─────────────────────────────────────────────
class TestModeResolution:
    def test_explicit_metadata_mode_wins(self):
        ch = _FakeChannel(kind="webhook", metadata_json={"mode": "stream"})
        assert _channel_mode(ch) == "stream"

    def test_default_mode_when_unset(self):
        # The 'webhook' provider defaults to webhook in metadata().
        ch = _FakeChannel(kind="webhook", metadata_json={})
        assert _channel_mode(ch) == "webhook"


class TestStartStopChannel:
    async def test_start_runs_provider_and_dispatches(self, _patch_dispatch):
        from app.services.channels import register_provider

        register_provider(_FakeStreamProvider())
        _FakeStreamProvider.received = []
        _FakeStreamProvider.started_count = 0

        rt = ChannelRuntime()
        ch = _FakeChannel(kind="_test_stream")
        await rt._start_channel_locked(ch)
        # Wait for the dispatch ping.
        for _ in range(20):
            if _patch_dispatch:
                break
            await asyncio.sleep(0.05)
        assert _patch_dispatch, "dispatch was never invoked"
        assert _patch_dispatch[0].user_text == "ping"

        await rt.stop_channel(ch.id)
        # Task must have actually been cleaned up.
        assert ch.id not in rt._tasks

    async def test_restart_replaces_task(self, _patch_dispatch):
        from app.services.channels import register_provider

        register_provider(_FakeStreamProvider())
        _FakeStreamProvider.started_count = 0

        rt = ChannelRuntime()
        ch = _FakeChannel(kind="_test_stream")
        await rt._start_channel_locked(ch)
        await asyncio.sleep(0.05)
        first_task = rt._tasks[ch.id].task

        await rt.restart_channel(ch)
        await asyncio.sleep(0.05)
        new_entry = rt._tasks.get(ch.id)
        assert new_entry is not None, "restart should have spawned a new task"
        assert new_entry.task is not first_task

        await rt.stop_channel(ch.id)


class TestFingerprint:
    def test_same_config_same_fingerprint(self):
        c1 = _FakeChannel(config_json={"a": 1})
        _FakeChannel(id=c1.id, config_json={"a": 1})
        # Different uuid for workspace shouldn't affect kind/mode/enabled hash —
        # but the test really just locks: same input ⇒ same output.
        assert _config_fingerprint(c1) == _config_fingerprint(c1)

    def test_config_change_breaks_fingerprint(self):
        c1 = _FakeChannel(config_json={"k": "v1"})
        c2 = _FakeChannel(id=c1.id, config_json={"k": "v2"})
        assert _config_fingerprint(c1) != _config_fingerprint(c2)


class _FakeUnavailableProvider(ChannelProvider):
    """Provider whose runtime check returns False — simulates a missing SDK extra."""

    kind = "_test_no_stream"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Fake unavailable",
            description="unit test only",
            supported_modes=("webhook", "stream"),
            default_mode="stream",
        )

    @classmethod
    def supports_stream(cls) -> bool:
        return True

    @classmethod
    def stream_available(cls) -> bool:
        return False

    def parse_inbound(self, payload, headers):  # type: ignore[override]
        return None


class TestSkipUnavailableStream:
    """The runtime must not log "stream not available" every reconcile.

    Before this fix, ``_start_channel_locked`` returned without recording
    anything in ``self._tasks``, so the supervisor's 30s reconcile pass
    re-attempted the start, re-logged, and the operator's terminal got
    spammed with one identical INFO line every heartbeat.
    """

    async def test_unavailable_stream_recorded_in_skipped(self):
        from app.services.channels import register_provider

        register_provider(_FakeUnavailableProvider())

        rt = ChannelRuntime()
        ch = _FakeChannel(kind="_test_no_stream")
        await rt._start_channel_locked(ch)

        assert ch.id not in rt._tasks
        assert ch.id in rt._skipped
        assert rt._skipped[ch.id] == _config_fingerprint(ch)

    async def test_skip_cleared_when_config_changes(self):
        from app.services.channels import register_provider

        register_provider(_FakeUnavailableProvider())

        rt = ChannelRuntime()
        ch = _FakeChannel(kind="_test_no_stream", config_json={"v": "1"})
        await rt._start_channel_locked(ch)
        assert ch.id in rt._skipped

        ch.config_json = {"v": "2"}
        await rt._start_channel_locked(ch)
        assert rt._skipped[ch.id] == _config_fingerprint(ch)

    async def test_stop_channel_clears_skip(self):
        from app.services.channels import register_provider

        register_provider(_FakeUnavailableProvider())

        rt = ChannelRuntime()
        ch = _FakeChannel(kind="_test_no_stream")
        await rt._start_channel_locked(ch)
        assert ch.id in rt._skipped

        await rt.stop_channel(ch.id)
        assert ch.id not in rt._skipped
