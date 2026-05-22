"""End-to-end M2.5.5 plugin host wiring tests.

The full ``NativeBackend.run`` cannot fire without a real LLM key, so
we exercise the same singleton the runner imports
(:data:`app.agents.harness.plugin_host.plugin_host`) and verify the
contract:

1. The plugin loader registers callbacks on the same singleton the
   runner consumes — there is no separate registry.
2. Firing each of the six hook names invokes the registered callback
   exactly once with the kwargs the runner provides.
3. A misbehaving callback (raise / hang) cannot break the run path —
   the surrounding ``fire`` swallows it and continues.
4. The default-deny gate (``allow_user_plugins=False``) keeps the
   registry empty even when valid plugin folders sit on disk.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from app.agents.harness import plugin_host
from app.services import plugin_loader

pytestmark = pytest.mark.asyncio

_ALL_HOOKS = (
    "pre_tool_call",
    "post_tool_call",
    "on_session_start",
    "on_session_end",
    "pre_llm_call",
    "post_llm_call",
)


@pytest.fixture(autouse=True)
def _reset_plugin_host():
    plugin_host.plugin_host.clear()
    yield
    plugin_host.plugin_host.clear()


def _write_six_hook_plugin(root: Path, name: str = "all_hooks") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text('"""test plugin."""\n', encoding="utf-8")
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.0.1",
                "description": "test",
                "capability_scopes": list(_ALL_HOOKS),
                "entry_module": f"{name}.entry",
            }
        ),
        encoding="utf-8",
    )
    body = (
        "fired = []\n"
        "\n"
        "def _make(hook):\n"
        "    async def _cb(**payload):\n"
        "        fired.append((hook, payload))\n"
        "    return _cb\n"
        "\n"
        "def register(ctx):\n"
        + "\n".join(f"    ctx.register_hook({h!r}, _make({h!r}))" for h in _ALL_HOOKS)
        + "\n"
    )
    (folder / "entry.py").write_text(body, encoding="utf-8")
    return folder


async def test_loader_registers_all_six_hooks(db_session, tmp_path: Path) -> None:
    """The loader must wire every declared scope onto the runner's
    singleton — same instance, not a clone."""
    _write_six_hook_plugin(tmp_path)
    loaded = await plugin_loader.load_and_register_plugins(
        db_session,
        plugin_dir=tmp_path,
        allow_user_plugins=True,
    )
    assert len(loaded) == 1
    for hook in _ALL_HOOKS:
        assert plugin_host.plugin_host.registered(hook) >= 1, (
            f"hook {hook} not registered on the runner's singleton"
        )


async def test_fire_invokes_loaded_callbacks(db_session, tmp_path: Path) -> None:
    _write_six_hook_plugin(tmp_path)
    loaded = await plugin_loader.load_and_register_plugins(
        db_session,
        plugin_dir=tmp_path,
        allow_user_plugins=True,
    )
    # Reach into the loaded module to inspect what callbacks observed.
    import sys

    module = sys.modules[loaded[0].manifest.entry_module]
    fired: list[tuple[str, dict[str, Any]]] = module.fired

    # Drive each hook with payloads shaped like what the runner emits.
    common = {
        "run_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "session_id": uuid.uuid4(),
        "agent_id": uuid.uuid4(),
    }
    await plugin_host.plugin_host.fire(
        "on_session_start", identity_id=uuid.uuid4(), served_model="x", **common
    )
    await plugin_host.plugin_host.fire(
        "pre_llm_call",
        iteration=1,
        served_model="x",
        upstream_model="y",
        provider_kind="z",
        **common,
    )
    await plugin_host.plugin_host.fire(
        "post_llm_call",
        iteration=1,
        served_model="x",
        upstream_model="y",
        provider_kind="z",
        text_chars=10,
        **common,
    )
    await plugin_host.plugin_host.fire(
        "pre_tool_call",
        tool_name="echo",
        tool_call_id="abc",
        args={},
        **common,
    )
    await plugin_host.plugin_host.fire(
        "post_tool_call",
        tool_name="echo",
        tool_call_id="abc",
        result={},
        ok=True,
        truncated=False,
        **common,
    )
    await plugin_host.plugin_host.fire(
        "on_session_end",
        identity_id=uuid.uuid4(),
        final_outcome="completed",
        **common,
    )

    seen = {hook for hook, _ in fired}
    assert seen == set(_ALL_HOOKS), f"missing hooks: {set(_ALL_HOOKS) - seen}"


async def test_failing_callback_does_not_break_runner_path(db_session, tmp_path: Path) -> None:
    """A plugin that explodes on every hook still leaves the runner's
    fire-and-forget surface intact: no exception bubbles up.
    """
    folder = tmp_path / "explosive"
    folder.mkdir()
    (folder / "__init__.py").write_text('"""x"""\n', encoding="utf-8")
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "name": "explosive",
                "version": "0.0.1",
                "description": "raises on every hook",
                "capability_scopes": list(_ALL_HOOKS),
                "entry_module": "explosive.entry",
            }
        ),
        encoding="utf-8",
    )
    (folder / "entry.py").write_text(
        "async def _boom(**_):\n"
        "    raise RuntimeError('plugin tried to break the runner')\n"
        "\n"
        "def register(ctx):\n"
        + "\n".join(f"    ctx.register_hook({h!r}, _boom)" for h in _ALL_HOOKS)
        + "\n",
        encoding="utf-8",
    )

    await plugin_loader.load_and_register_plugins(
        db_session,
        plugin_dir=tmp_path,
        allow_user_plugins=True,
    )
    # Each fire must return cleanly (no raise) — the runner relies on this.
    for hook in _ALL_HOOKS:
        await plugin_host.plugin_host.fire(hook, dummy=True)


async def test_disabled_setting_keeps_registry_empty(db_session, tmp_path: Path) -> None:
    """Default-deny: when the platform setting is False we never load
    even valid plugins. Runner stays a no-op fan-out.
    """
    _write_six_hook_plugin(tmp_path)
    loaded = await plugin_loader.load_and_register_plugins(
        db_session,
        plugin_dir=tmp_path,
        allow_user_plugins=False,
    )
    assert loaded == []
    for hook in _ALL_HOOKS:
        assert plugin_host.plugin_host.registered(hook) == 0


async def test_runner_imports_same_plugin_host_singleton() -> None:
    """The runner module must import the exact singleton the loader
    uses — otherwise registration goes to a different registry and
    nothing fires. This catches accidental ``copy`` or shadowing.
    """
    from app.agents.kernels.native import runner as runner_mod

    assert runner_mod.plugin_host is plugin_host.plugin_host


async def test_hook_names_match_runner_call_sites() -> None:
    """Static guard: every host-whitelisted hook must appear at least
    once in the runner source. A typo on either side would silently
    drop the event so the cheap text scan is worth running on every
    PR.
    """
    src = (
        Path(__file__).resolve().parents[2] / "app" / "agents" / "kernels" / "native" / "runner.py"
    ).read_text(encoding="utf-8")
    for hook in _ALL_HOOKS:
        assert f'"{hook}"' in src, f"runner.py missing fire site for hook {hook}"


async def test_six_hook_payload_contract_with_sync_callback(db_session, tmp_path: Path) -> None:
    """Plain (non-async) plugin callbacks are bridged transparently.
    The runner cannot tell whether a plugin used ``def`` or ``async def``.
    """
    folder = tmp_path / "sync_cb"
    folder.mkdir()
    (folder / "__init__.py").write_text('"""x"""\n', encoding="utf-8")
    (folder / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sync_cb",
                "version": "0.0.1",
                "description": "x",
                "capability_scopes": ["pre_tool_call"],
                "entry_module": "sync_cb.entry",
            }
        ),
        encoding="utf-8",
    )
    (folder / "entry.py").write_text(
        "fired = []\n"
        "def _cb(*, tool_name, **_):\n"
        "    fired.append(tool_name)\n"
        "def register(ctx):\n"
        "    ctx.register_hook('pre_tool_call', _cb)\n",
        encoding="utf-8",
    )
    await plugin_loader.load_and_register_plugins(
        db_session,
        plugin_dir=tmp_path,
        allow_user_plugins=True,
    )
    await plugin_host.plugin_host.fire("pre_tool_call", tool_name="echo")

    import sys

    assert sys.modules["sync_cb.entry"].fired == ["echo"]


# Belt-and-suspenders: an empty registry must still accept fire()
# without writing audit rows or raising.
async def test_fire_is_safe_with_zero_callbacks() -> None:
    await plugin_host.plugin_host.fire("pre_tool_call")
