"""DB-backed integration tests for the M3.9 signing pipeline.

Drives ``load_and_register_plugins(allow_user_plugins=None)`` —
the production path that reads ``platform_settings.plugins`` and
gates each plugin via ``evaluate_plugin_for_load``. Each test sets
up a fresh plugin folder + signature + (optionally) a registry row,
then asserts the loader's audit fan-out + register outcome.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.agents.harness import plugin_host
from app.db.models.audit import AuditEvent
from app.db.models.plugin_registry import PluginRegistry, PluginRegistryStatus
from app.services import platform_settings as ps_svc
from app.services import plugin_loader as loader

pynacl = pytest.importorskip("nacl.signing")
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_plugin_host():
    plugin_host.plugin_host.clear()
    yield
    plugin_host.plugin_host.clear()


def _signing_pair() -> tuple[str, pynacl.SigningKey]:
    sk = pynacl.SigningKey.generate()
    pubkey_b64 = base64.b64encode(bytes(sk.verify_key)).decode("ascii")
    return pubkey_b64, sk


def _hash_folder(folder: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.endswith(".sig"):
            continue
        rel = path.relative_to(folder).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\xff")
    return digest.hexdigest()


def _write_plugin(
    root: Path,
    name: str = "alpha",
    *,
    sign_with: pynacl.SigningKey | None = None,
) -> tuple[Path, str]:
    """Lay down a plugin folder + optional signature; return (folder, sha)."""
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "__init__.py").write_text('"""x"""\n', encoding="utf-8")
    manifest = {
        "name": name,
        "version": "0.0.1",
        "description": "test",
        "capability_scopes": ["pre_tool_call"],
        "entry_module": f"{name}.entry",
    }
    manifest_path = folder / "plugin.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "entry.py").write_text(
        "async def _hook(**_):\n"
        "    return None\n"
        "\n"
        "def register(ctx):\n"
        "    ctx.register_hook('pre_tool_call', _hook)\n",
        encoding="utf-8",
    )
    sha = _hash_folder(folder)
    if sign_with is not None:
        sig = sign_with.sign(sha.encode("utf-8")).signature
        sig_b64 = base64.b64encode(sig).decode("ascii")
        (folder / f"{manifest_path.name}.sig").write_text(sig_b64, encoding="utf-8")
    return folder, sha


async def _set_plugins_settings(
    db_session,
    identity,
    *,
    allow_user_plugins: bool,
    allow_unapproved_plugins: bool = False,
    signing_root_pubkey: str | None = None,
) -> None:
    ps_svc.invalidate_local()
    await ps_svc.update_section(
        db_session,
        section=ps_svc.PlatformSettingsSection.PLUGINS,
        payload={
            "allow_user_plugins": allow_user_plugins,
            "allow_unapproved_plugins": allow_unapproved_plugins,
            "signing_root_pubkey": signing_root_pubkey,
            "auto_reload_on_admin_approve": True,
        },
        actor_identity_id=identity.id,
        confirmed_dangerous=True,
    )
    await db_session.commit()
    ps_svc.invalidate_local()


async def _audit_actions_for_plugin(db_session, plugin_name: str) -> list[str]:
    """Return every plugin-resource audit action in this session.

    We don't filter by name because some audit rows (e.g.
    ``plugin.disabled_by_platform_setting``) carry no plugin name —
    they're emitted before any per-plugin work happens.
    """
    rows = (
        (await db_session.execute(select(AuditEvent).where(AuditEvent.resource_type == "plugin")))
        .scalars()
        .all()
    )
    return [r.action for r in rows]


# ── Test: master switch off ─────────────────────────────────
async def test_load_disabled_zero_loaded_plus_audit(db_session, identity, tmp_path: Path) -> None:
    await _set_plugins_settings(db_session, identity, allow_user_plugins=False)
    _write_plugin(tmp_path)

    loaded = await loader.load_and_register_plugins(db_session, plugin_dir=tmp_path)
    assert loaded == []
    actions = await _audit_actions_for_plugin(db_session, "alpha")
    # Production path: discovery still emits plugin.discovered for the
    # row and evaluate returns disabled. (Or the lifespan short-circuit
    # path emits plugin.disabled_by_platform_setting alone — exercised
    # by the dedicated lifespan test.)
    assert "plugin.discovered" in actions
    assert "plugin.disabled_by_platform_setting" in actions


# ── Test: trust root absent ─────────────────────────────────
async def test_load_no_trust_root(db_session, identity, tmp_path: Path) -> None:
    await _set_plugins_settings(
        db_session,
        identity,
        allow_user_plugins=True,
        signing_root_pubkey=None,
    )
    _write_plugin(tmp_path)
    loaded = await loader.load_and_register_plugins(db_session, plugin_dir=tmp_path)
    assert loaded == []
    actions = await _audit_actions_for_plugin(db_session, "alpha")
    assert "plugin.no_trust_root" in actions


# ── Test: signed + approved plugin loads ────────────────────
async def test_load_signed_and_approved_succeeds(db_session, identity, tmp_path: Path) -> None:
    pubkey, sk = _signing_pair()
    await _set_plugins_settings(
        db_session,
        identity,
        allow_user_plugins=True,
        signing_root_pubkey=pubkey,
    )
    _, sha = _write_plugin(tmp_path, sign_with=sk)

    db_session.add(
        PluginRegistry(
            name="alpha",
            version="0.0.1",
            sha256=sha,
            signature=None,
            capability_scopes=["pre_tool_call"],
            approved_by_platform_admin=True,
            approved_by_identity_id=identity.id,
            status=PluginRegistryStatus.APPROVED,
        )
    )
    await db_session.flush()
    await db_session.commit()

    loaded = await loader.load_and_register_plugins(db_session, plugin_dir=tmp_path)
    assert [p.manifest.name for p in loaded] == ["alpha"]
    assert plugin_host.plugin_host.registered("pre_tool_call") == 1

    actions = await _audit_actions_for_plugin(db_session, "alpha")
    assert "plugin.signature_verified" in actions
    assert "plugin.loaded" in actions

    refreshed = (
        await db_session.execute(
            select(PluginRegistry).where(
                PluginRegistry.name == "alpha",
                PluginRegistry.sha256 == sha,
            )
        )
    ).scalar_one()
    assert refreshed.status == PluginRegistryStatus.LOADED


# ── Test: signed but not approved → skip ────────────────────
async def test_load_signed_but_unapproved_skipped(db_session, identity, tmp_path: Path) -> None:
    pubkey, sk = _signing_pair()
    await _set_plugins_settings(
        db_session,
        identity,
        allow_user_plugins=True,
        signing_root_pubkey=pubkey,
    )
    _, sha = _write_plugin(tmp_path, sign_with=sk)

    db_session.add(
        PluginRegistry(
            name="alpha",
            version="0.0.1",
            sha256=sha,
            capability_scopes=["pre_tool_call"],
            approved_by_platform_admin=False,
            status=PluginRegistryStatus.DISCOVERED,
        )
    )
    await db_session.flush()
    await db_session.commit()

    loaded = await loader.load_and_register_plugins(db_session, plugin_dir=tmp_path)
    assert loaded == []
    assert plugin_host.plugin_host.registered("pre_tool_call") == 0

    actions = await _audit_actions_for_plugin(db_session, "alpha")
    assert "plugin.not_approved" in actions
    assert "plugin.loaded" not in actions


# ── Test: signature missing when pubkey present ─────────────
async def test_load_signature_missing(db_session, identity, tmp_path: Path) -> None:
    pubkey, _ = _signing_pair()
    await _set_plugins_settings(
        db_session,
        identity,
        allow_user_plugins=True,
        signing_root_pubkey=pubkey,
    )
    # Write the plugin without a signature file.
    _write_plugin(tmp_path, sign_with=None)
    loaded = await loader.load_and_register_plugins(db_session, plugin_dir=tmp_path)
    assert loaded == []
    actions = await _audit_actions_for_plugin(db_session, "alpha")
    assert "plugin.signature_missing" in actions


# ── Test: dev-mode escape ───────────────────────────────────
async def test_load_dev_mode_skips_signature(db_session, identity, tmp_path: Path) -> None:
    """``allow_unapproved_plugins=True`` lets unsigned plugins load
    without a registry row. Production must keep this off.
    """
    await _set_plugins_settings(
        db_session,
        identity,
        allow_user_plugins=True,
        allow_unapproved_plugins=True,
        signing_root_pubkey=None,
    )
    _write_plugin(tmp_path, sign_with=None)
    loaded = await loader.load_and_register_plugins(db_session, plugin_dir=tmp_path)
    assert [p.manifest.name for p in loaded] == ["alpha"]
    assert plugin_host.plugin_host.registered("pre_tool_call") == 1
