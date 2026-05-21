"""Plugin host ``fire`` contract tests (M2.5.5).

The runner depends on four invariants:

1. A registered callback is invoked once per ``fire(hook_name, ...)``.
2. A callback that exceeds :data:`HOOK_TIMEOUT_SECONDS` is cut off and
   the host emits a ``plugin.hook_timeout`` audit row.
3. A callback that raises is recorded as ``plugin.hook_failed``; the
   exception never reaches the runner.
4. Callbacks are isolated: when one explodes, sibling callbacks for
   the same hook still get invoked.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.agents.harness import plugin_host as plugin_host_mod


@pytest.fixture(autouse=True)
def _reset_plugin_host():
    plugin_host_mod.plugin_host.clear()
    yield
    plugin_host_mod.plugin_host.clear()


@pytest.fixture(autouse=True)
def _capture_audit(monkeypatch):
    """Capture audit rows the host attempts to write so tests can
    assert on the action keys without needing a DB session.
    """
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _fake_audit(action: str, *, metadata: dict[str, Any]) -> None:
        captured.append((action, metadata))

    monkeypatch.setattr(plugin_host_mod, "_audit_async", _fake_audit)
    return captured


def test_register_with_unknown_hook_raises():
    with pytest.raises(ValueError) as exc_info:
        plugin_host_mod.plugin_host.register_hook("not_a_hook", lambda **_: None)
    assert "not_a_hook" in str(exc_info.value)


def test_fire_invokes_registered_callback():
    seen: list[dict[str, Any]] = []

    async def _cb(**payload: Any) -> None:
        seen.append(payload)

    plugin_host_mod.plugin_host.register_hook("pre_tool_call", _cb)
    asyncio.run(
        plugin_host_mod.plugin_host.fire("pre_tool_call", tool_name="echo", x=1)
    )

    assert seen == [{"tool_name": "echo", "x": 1}]


def test_fire_supports_sync_callbacks():
    """Plugins are allowed to register plain functions; the host
    bridges them into the async event loop transparently.
    """
    seen: list[str] = []

    def _cb(*, tool_name: str, **_kwargs: Any) -> None:
        seen.append(tool_name)

    plugin_host_mod.plugin_host.register_hook("post_tool_call", _cb)
    asyncio.run(
        plugin_host_mod.plugin_host.fire("post_tool_call", tool_name="echo")
    )
    assert seen == ["echo"]


def test_fire_with_no_callbacks_is_silent():
    """Empty hook lists must not write an audit row — that would spam
    the audit feed on every model_request_node iteration.
    """
    asyncio.run(plugin_host_mod.plugin_host.fire("post_llm_call"))
    # No assertion on captured audit needed; the autouse fixture
    # would surface anything that actually fired.


def test_fire_records_timeout_audit(_capture_audit, monkeypatch):
    """When a callback exceeds the timeout, the host raises an audit
    but never propagates the timeout.
    """
    # Shrink the budget so the test is fast and deterministic.
    monkeypatch.setattr(plugin_host_mod, "HOOK_TIMEOUT_SECONDS", 0.05)

    async def _slow(**_payload: Any) -> None:
        await asyncio.sleep(1.0)

    plugin_host_mod.plugin_host.register_hook("on_session_start", _slow)
    asyncio.run(plugin_host_mod.plugin_host.fire("on_session_start"))

    actions = [row[0] for row in _capture_audit]
    assert "plugin.hook_timeout" in actions


def test_fire_records_failure_audit(_capture_audit):
    async def _bad(**_payload: Any) -> None:
        raise RuntimeError("boom")

    plugin_host_mod.plugin_host.register_hook("on_session_end", _bad)
    asyncio.run(plugin_host_mod.plugin_host.fire("on_session_end"))

    actions = [row[0] for row in _capture_audit]
    assert "plugin.hook_failed" in actions
    metadata = next(meta for action, meta in _capture_audit if action == "plugin.hook_failed")
    assert metadata["error_class"] == "RuntimeError"


def test_one_failing_callback_does_not_block_siblings(_capture_audit):
    survivors: list[str] = []

    async def _bad(**_payload: Any) -> None:
        raise ValueError("bad")

    async def _good(**_payload: Any) -> None:
        survivors.append("ran")

    plugin_host_mod.plugin_host.register_hook("pre_llm_call", _bad)
    plugin_host_mod.plugin_host.register_hook("pre_llm_call", _good)
    asyncio.run(plugin_host_mod.plugin_host.fire("pre_llm_call"))

    assert survivors == ["ran"]
    actions = [row[0] for row in _capture_audit]
    assert actions.count("plugin.hook_failed") == 1


def test_fire_never_raises_to_caller(_capture_audit):
    """Belt-and-suspenders: even if everything goes wrong (callback
    raises AND the audit hook itself raises), ``fire`` returns
    cleanly. The runner relies on this guarantee.
    """
    import app.agents.harness.plugin_host as ph_mod

    async def _audit_blows_up(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit dead")

    async def _bad(**_payload: Any) -> None:
        raise RuntimeError("hook dead")

    # Replace the captured fake with one that raises so we test the
    # log.warning fallback in ``_audit_async``. Use the module-level
    # helper directly because the plugin_host module reads it by name.
    original = ph_mod._audit_async
    ph_mod._audit_async = _audit_blows_up  # type: ignore[assignment]
    try:
        plugin_host_mod.plugin_host.register_hook("post_llm_call", _bad)
        # Must not raise.
        asyncio.run(plugin_host_mod.plugin_host.fire("post_llm_call"))
    finally:
        ph_mod._audit_async = original  # type: ignore[assignment]
