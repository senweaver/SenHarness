"""Plugin loader unit tests (M2.5.5).

Cover the four contract points the host depends on:

1. ``discover_plugins`` finds a well-formed manifest, computes a
   stable sha256, and resolves the ``register`` callable.
2. A folder without a manifest is silently skipped (operators stage
   in-progress plugins this way).
3. A manifest that names an unknown hook in ``capability_scopes`` is
   rejected at parse time.
4. ``load_and_register_plugins`` is a strict no-op when
   ``allow_user_plugins=False`` — design principle 7's default-deny
   gate must hold even when there are valid manifests on disk.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.harness import plugin_host
from app.services import plugin_loader as loader


@pytest.fixture(autouse=True)
def _reset_plugin_host():
    plugin_host.plugin_host.clear()
    yield
    plugin_host.plugin_host.clear()


def _write_plugin(
    root: Path,
    name: str,
    *,
    capability_scopes: list[str] | None = None,
    register_body: str = "",
    manifest_format: str = "json",
    extra_files: dict[str, str] | None = None,
    omit_manifest: bool = False,
    omit_register: bool = False,
    entry_module: str | None = None,
) -> Path:
    """Lay down a plugin folder shaped like the runtime expects.

    Defaults emit a JSON manifest so the test suite is independent of
    PyYAML; the loader reads JSON, YAML, and TOML the same way.
    """
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text(
        "\"\"\"test plugin.\"\"\"\n", encoding="utf-8"
    )
    if entry_module is None:
        entry_module = f"{name}.entry"
    if not omit_manifest:
        manifest: dict[str, Any] = {
            "name": name,
            "version": "0.0.1",
            "description": "test plugin",
            "capability_scopes": capability_scopes
            if capability_scopes is not None
            else ["pre_tool_call"],
            "entry_module": entry_module,
        }
        if manifest_format == "json":
            (folder / "plugin.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
        else:
            raise ValueError(f"unsupported manifest_format {manifest_format!r}")
    if not omit_register:
        body = register_body or (
            "async def _hook(**_):\n"
            "    return None\n"
            "\n"
            "def register(ctx):\n"
            "    ctx.register_hook('pre_tool_call', _hook)\n"
        )
        (folder / "entry.py").write_text(body, encoding="utf-8")
    for path, content in (extra_files or {}).items():
        (folder / path).write_text(content, encoding="utf-8")
    return folder


def test_discover_finds_manifest_and_computes_sha256(tmp_path: Path) -> None:
    folder = _write_plugin(tmp_path, "alpha")

    discovered = asyncio.run(loader.discover_plugins(tmp_path))

    assert len(discovered) == 1
    plugin = discovered[0]
    assert plugin.manifest.name == "alpha"
    assert plugin.manifest.version == "0.0.1"
    assert plugin.manifest.capability_scopes == ("pre_tool_call",)
    assert plugin.manifest.entry_module == "alpha.entry"
    assert plugin.folder == folder
    assert callable(plugin.register_func)
    # SHA-256 hex is 64 chars; tampering any file in the folder
    # changes the digest deterministically.
    assert isinstance(plugin.sha256, str)
    assert len(plugin.sha256) == 64
    (folder / "entry.py").write_text(
        "def register(ctx):\n    pass\n", encoding="utf-8"
    )
    redigested = asyncio.run(loader.discover_plugins(tmp_path))
    assert redigested[0].sha256 != plugin.sha256


def test_discover_skips_folders_without_manifest(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "alpha")
    _write_plugin(tmp_path, "beta", omit_manifest=True)
    # Hidden / private folders never count.
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "_staging").mkdir()

    discovered = asyncio.run(loader.discover_plugins(tmp_path))

    names = sorted(p.manifest.name for p in discovered)
    assert names == ["alpha"]


def test_manifest_with_unknown_capability_scope_is_rejected(
    tmp_path: Path,
) -> None:
    _write_plugin(tmp_path, "alpha", capability_scopes=["nope"])

    discovered = asyncio.run(loader.discover_plugins(tmp_path))

    assert len(discovered) == 1
    assert discovered[0].error is not None
    assert "capability_scopes" in discovered[0].error
    assert discovered[0].register_func is None


def test_register_outside_declared_scope_raises_in_context(
    tmp_path: Path,
) -> None:
    """``register_hook`` enforces the manifest at runtime too — a
    plugin that declares only ``pre_tool_call`` cannot wire
    ``post_llm_call`` no matter what its register() body says.
    """
    _write_plugin(
        tmp_path,
        "alpha",
        capability_scopes=["pre_tool_call"],
        register_body=(
            "async def _hook(**_):\n"
            "    return None\n"
            "\n"
            "def register(ctx):\n"
            "    ctx.register_hook('post_llm_call', _hook)\n"
        ),
    )

    discovered = asyncio.run(loader.discover_plugins(tmp_path))
    plugin = discovered[0]
    ctx = loader.PluginContext(manifest=plugin.manifest)
    with pytest.raises(ValueError) as exc_info:
        plugin.register_func(ctx)  # type: ignore[misc]
    assert "capability_scopes" in str(exc_info.value)


def test_load_and_register_is_no_op_when_disabled(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "alpha")
    audited: list[tuple[str, dict[str, Any]]] = []

    class _StubAuditService:
        async def record(self, _db, **kwargs: Any) -> None:
            audited.append((kwargs.get("action", ""), dict(kwargs)))

    class _StubSession:
        async def commit(self) -> None:
            return None

    # Patch the audit service the loader imports so the test stays
    # purely in-memory — the plugin folder is real but the DB is not.
    import app.services.plugin_loader as plugin_loader_mod

    original = plugin_loader_mod.audit_svc
    plugin_loader_mod.audit_svc = _StubAuditService()  # type: ignore[assignment]
    try:
        loaded = asyncio.run(
            loader.load_and_register_plugins(
                _StubSession(),  # type: ignore[arg-type]
                plugin_dir=tmp_path,
                allow_user_plugins=False,
            )
        )
    finally:
        plugin_loader_mod.audit_svc = original  # type: ignore[assignment]

    assert loaded == []
    # Default-deny audit must land so operators see why nothing loaded.
    actions = [row[0] for row in audited]
    assert "plugin.disabled_by_platform_setting" in actions
    # And the host registry must be empty — not a single hook installed.
    for hook in plugin_host.HOOK_NAMES:
        assert plugin_host.plugin_host.registered(hook) == 0


def test_load_and_register_audits_each_loaded_plugin(tmp_path: Path) -> None:
    _write_plugin(tmp_path, "alpha")
    _write_plugin(
        tmp_path,
        "broken",
        capability_scopes=["pre_tool_call"],
        register_body="def register(ctx):\n    raise RuntimeError('boom')\n",
    )
    audited: list[tuple[str, dict[str, Any]]] = []

    class _StubAuditService:
        async def record(self, _db, **kwargs: Any) -> None:
            audited.append((kwargs.get("action", ""), dict(kwargs)))

    class _StubSession:
        async def commit(self) -> None:
            return None

    import app.services.plugin_loader as plugin_loader_mod

    original = plugin_loader_mod.audit_svc
    plugin_loader_mod.audit_svc = _StubAuditService()  # type: ignore[assignment]
    try:
        loaded = asyncio.run(
            loader.load_and_register_plugins(
                _StubSession(),  # type: ignore[arg-type]
                plugin_dir=tmp_path,
                allow_user_plugins=True,
            )
        )
    finally:
        plugin_loader_mod.audit_svc = original  # type: ignore[assignment]

    actions = [row[0] for row in audited]
    assert "plugin.loaded" in actions
    assert "plugin.load_failed" in actions
    # Only the alpha plugin actually wired a hook.
    assert [p.manifest.name for p in loaded] == ["alpha"]
    assert plugin_host.plugin_host.registered("pre_tool_call") == 1
