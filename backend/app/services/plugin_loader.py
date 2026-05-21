"""Filesystem-based plugin discovery + load (M2.5.5 / M3.5 / M3.9).

Plugins live under ``STORAGE_LOCAL_PATH/plugins/<name>/`` and ship at
minimum:

* ``plugin.yaml`` (or ``plugin.toml`` / ``plugin.json``) — manifest
  with metadata the host needs to gate registration.
* ``__init__.py`` plus a Python module that exposes
  ``def register(ctx)``. The :class:`PluginContext` it receives wires
  the plugin's hooks / tools / channel kinds / model provider kinds
  into the runtime.

M3.9 layered ed25519 signature verification + admin approval on top:
plugins drop a sibling ``plugin.yaml.sig`` file with a base64
signature of the folder ``sha256``. The loader reads
``platform_settings.plugins`` (master switch + dev-mode escape +
trust root pubkey), calls
:func:`app.services.plugin_signing.evaluate_plugin_for_load` per
plugin, and only invokes ``register(ctx)`` when the gate returns
``allowed=True``. Every reject branch lands as a stable audit code so
operators can replay the decision.

Discovery is intentionally limited: we never scan PyPI, never look at
Python entry-points, and never load anything outside the configured
directory. The platform admin must explicitly drop a folder onto the
filesystem AND flip
``platform_settings.plugins.allow_user_plugins`` to ``True`` AND
either approve a :class:`PluginRegistry` row or set
``allow_unapproved_plugins=True`` for dev mode.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness.plugin_host import HOOK_NAMES, plugin_host
from app.db.models.plugin_registry import PluginRegistry, PluginRegistryStatus
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


# ── Manifest ────────────────────────────────────────────────
@dataclass(slots=True)
class PluginManifest:
    """Validated metadata block read from ``plugin.yaml``.

    ``capability_scopes`` is the gate the host enforces on register:
    a callback that targets a hook outside the declared scopes is
    refused. Beyond the original six pydantic-ai hook names M3.5
    promotes two registry namespaces — ``register_channel`` and
    ``register_model_provider`` — so a plugin that wants to install
    a new channel kind must declare ``register_channel`` and one
    that wants a new model provider must declare
    ``register_model_provider``.
    """

    name: str
    version: str
    description: str
    capability_scopes: tuple[str, ...]
    entry_module: str


@dataclass(slots=True)
class LoadedPlugin:
    manifest: PluginManifest
    sha256: str
    folder: Path
    register_func: Callable[[PluginContext], None] | None = None
    error: str | None = None
    registered_hooks: tuple[str, ...] = field(default_factory=tuple)
    registered_channels: tuple[str, ...] = field(default_factory=tuple)
    registered_providers: tuple[str, ...] = field(default_factory=tuple)
    signature: str | None = None
    registry_id: str | None = None


# ── Capability scope constants ──────────────────────────────
# Beyond the six runner hooks (HOOK_NAMES) M3.5 promotes two
# registry namespaces. They are listed separately so the manifest
# parser accepts them while the per-hook path keeps treating
# HOOK_NAMES as the runtime fire-site whitelist.
EXTRA_CAPABILITY_SCOPES: frozenset[str] = frozenset(
    {"register_channel", "register_model_provider", "register_tool"}
)
ALL_CAPABILITY_SCOPES: frozenset[str] = HOOK_NAMES | EXTRA_CAPABILITY_SCOPES


# ── Registration context handed to register(ctx) ─────────────
class PluginContext:
    """Surface plugins call into during ``register(ctx)``.

    Every method is gated by the plugin's declared
    ``capability_scopes``; an out-of-scope call raises ``ValueError``
    which the loader catches and converts into ``plugin.load_failed``.
    Plugins therefore can't lie about what they need by simply
    requesting more capabilities at runtime.
    """

    def __init__(self, *, manifest: PluginManifest) -> None:
        self._manifest = manifest
        self._hooks_registered: list[str] = []
        self._tools_registered: list[str] = []
        self._channels_registered: list[str] = []
        self._providers_registered: list[str] = []

    @property
    def hooks_registered(self) -> tuple[str, ...]:
        return tuple(self._hooks_registered)

    @property
    def tools_registered(self) -> tuple[str, ...]:
        return tuple(self._tools_registered)

    @property
    def channels_registered(self) -> tuple[str, ...]:
        return tuple(self._channels_registered)

    @property
    def providers_registered(self) -> tuple[str, ...]:
        return tuple(self._providers_registered)

    def register_hook(
        self,
        hook_name: str,
        callback: Callable[..., Awaitable[Any] | Any],
    ) -> None:
        if hook_name not in HOOK_NAMES:
            raise ValueError(
                f"plugin {self._manifest.name!r}: unknown hook {hook_name!r}; "
                f"valid={sorted(HOOK_NAMES)}"
            )
        if hook_name not in self._manifest.capability_scopes:
            raise ValueError(
                f"plugin {self._manifest.name!r}: hook {hook_name!r} not "
                f"declared in capability_scopes "
                f"({list(self._manifest.capability_scopes)})"
            )
        plugin_host.register_hook(hook_name, callback)
        self._hooks_registered.append(hook_name)

    def register_tool(
        self,
        name: str,
        args_model: Any,
        runner: Callable[..., Any],
    ) -> None:
        """Reserved hook for plugin-contributed tools.

        The runner's BUILTIN_TOOL_REGISTRY promotion is still M4
        work; M3.5 only records the request so the admin console
        can show what a plugin would have wired. Calls are still
        gated by ``register_tool`` capability scope.
        """
        if "register_tool" not in self._manifest.capability_scopes:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_tool requires "
                "'register_tool' in capability_scopes"
            )
        if not isinstance(name, str) or not name:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_tool requires a "
                "non-empty name"
            )
        log.info(
            "plugin %s requested tool %s (deferred to M4)",
            self._manifest.name,
            name,
        )
        self._tools_registered.append(name)

    def register_channel_kind(
        self,
        kind: str,
        factory: Callable[[], Any],
    ) -> None:
        """Register a new IM channel kind contributed by this plugin.

        The factory must be a zero-arg callable that returns a
        :class:`ChannelProvider` instance with ``provider.kind ==
        kind``. The host validates that ``kind`` is not already
        owned by a built-in channel before installing it; built-in
        kinds can never be overridden by a plugin (the audit trail
        for an inbound webhook would otherwise become ambiguous).
        """
        if "register_channel" not in self._manifest.capability_scopes:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_channel_kind "
                "requires 'register_channel' in capability_scopes "
                f"({list(self._manifest.capability_scopes)})"
            )
        if not isinstance(kind, str) or not kind:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_channel_kind "
                "requires a non-empty kind"
            )
        if not callable(factory):
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_channel_kind "
                "requires a callable factory"
            )

        from app.services.channels import register_provider_from_plugin

        provider = factory()
        register_provider_from_plugin(kind, provider)
        self._channels_registered.append(kind)

    def register_model_provider(
        self,
        kind: str,
        factory: Callable[[], Any],
    ) -> None:
        """Register a new model provider catalog entry.

        The factory returns a
        :class:`app.agents.kernels.provider_catalog.CatalogEntry`.
        The host refuses to override a built-in catalog entry for
        the same reason as channels — auditing model traffic against
        a stable catalog matters more than letting a plugin shadow
        OpenAI.
        """
        if "register_model_provider" not in self._manifest.capability_scopes:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_model_provider "
                "requires 'register_model_provider' in capability_scopes "
                f"({list(self._manifest.capability_scopes)})"
            )
        if not isinstance(kind, str) or not kind:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_model_provider "
                "requires a non-empty kind"
            )
        if not callable(factory):
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_model_provider "
                "requires a callable factory"
            )

        from app.agents.kernels.provider_catalog import register_kind_from_plugin

        entry = factory()
        register_kind_from_plugin(kind, entry)
        self._providers_registered.append(kind)

    # M2.5.5 surface preserved for back-compat — explicit
    # ``register_channel_kind`` / ``register_model_provider`` are
    # the M3.5 names. ``register_provider`` is kept as a thin
    # forwarder so plugins authored against the M2.5.5 API keep
    # working: the call audits as a tool / provider request without
    # mutating any registry.
    def register_provider(
        self,
        kind: str,
        factory: Callable[..., Any],
    ) -> None:
        if not isinstance(kind, str) or not kind:
            raise ValueError(
                f"plugin {self._manifest.name!r}: register_provider requires "
                "a non-empty kind"
            )
        log.info(
            "plugin %s requested provider kind=%s via legacy register_provider; "
            "use register_model_provider() to actually install it",
            self._manifest.name,
            kind,
        )
        self._providers_registered.append(kind)


# ── Manifest parsing ────────────────────────────────────────
_REQUIRED_MANIFEST_FIELDS: tuple[str, ...] = (
    "name",
    "version",
    "description",
    "capability_scopes",
    "entry_module",
)


def _parse_manifest_text(text: str, *, suffix: str) -> dict[str, Any]:
    """Parse a manifest blob.

    YAML is preferred; we lazy-import ``yaml`` and fall back to
    ``tomllib`` (stdlib) for ``.toml`` manifests so deployments that
    don't ship PyYAML can still load plugins.
    """
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — exercised by skipping plugins
            raise RuntimeError(
                "PyYAML is required to parse plugin.yaml; install pyyaml "
                "or rename the manifest to plugin.toml / plugin.json"
            ) from exc
        loaded = yaml.safe_load(text)
    elif suffix == ".toml":
        import tomllib

        loaded = tomllib.loads(text)
    elif suffix == ".json":
        loaded = json.loads(text)
    else:  # pragma: no cover — enforced by the discovery loop
        raise ValueError(f"unsupported manifest suffix {suffix!r}")
    if not isinstance(loaded, dict):
        raise ValueError("manifest top level must be a mapping")
    return loaded


def _coerce_manifest(raw: dict[str, Any]) -> PluginManifest:
    missing = [f for f in _REQUIRED_MANIFEST_FIELDS if f not in raw]
    if missing:
        raise ValueError(f"manifest missing required fields: {missing}")

    name = str(raw["name"]).strip()
    version = str(raw["version"]).strip()
    description = str(raw["description"]).strip()
    entry_module = str(raw["entry_module"]).strip()
    if not (name and version and description and entry_module):
        raise ValueError("manifest fields must be non-empty strings")

    scopes_raw = raw["capability_scopes"]
    if not isinstance(scopes_raw, (list, tuple)) or not scopes_raw:
        raise ValueError(
            "capability_scopes must be a non-empty list of hook / extension names"
        )
    scopes: list[str] = []
    for scope in scopes_raw:
        scope_str = str(scope).strip()
        if scope_str not in ALL_CAPABILITY_SCOPES:
            raise ValueError(
                f"capability_scopes contains unknown scope {scope_str!r}; "
                f"valid={sorted(ALL_CAPABILITY_SCOPES)}"
            )
        scopes.append(scope_str)

    return PluginManifest(
        name=name,
        version=version,
        description=description,
        capability_scopes=tuple(dict.fromkeys(scopes)),
        entry_module=entry_module,
    )


def _hash_plugin_folder(folder: Path) -> str:
    """SHA-256 over every regular file in ``folder``.

    Skips the sibling signature file so re-signing without changing
    the plugin payload doesn't shift the digest. Hashes filenames +
    contents so a rename is detected too.
    """
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        if rel.endswith(".sig"):
            continue
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65_536), b""):
                digest.update(chunk)
        digest.update(b"\xff")
    return digest.hexdigest()


def _find_manifest(plugin_dir: Path) -> Path | None:
    for candidate in ("plugin.yaml", "plugin.yml", "plugin.toml", "plugin.json"):
        path = plugin_dir / candidate
        if path.is_file():
            return path
    return None


def _read_signature(plugin_dir: Path, manifest_path: Path) -> str | None:
    """Read ``<manifest>.sig`` if present (e.g. ``plugin.yaml.sig``).

    The signature file is plain text — base64 of the ed25519
    signature over the folder ``sha256`` (the lowercase hex digest,
    UTF-8 encoded). Whitespace-only / missing files return ``None``
    so the gate's ``signature_missing`` branch can fire.
    """
    sig_path = plugin_dir / f"{manifest_path.name}.sig"
    if not sig_path.is_file():
        return None
    try:
        text = sig_path.read_text(encoding="utf-8").strip()
    except OSError:  # pragma: no cover - defensive
        return None
    return text or None


def _resolve_register_callable(
    folder: Path, manifest: PluginManifest
) -> Callable[[PluginContext], None]:
    """Import the plugin's entry module and return its ``register`` function."""
    parent_str = str(folder.parent.resolve())
    folder_str = str(folder.resolve())
    inserted: list[str] = []
    for candidate in (parent_str, folder_str):
        if candidate not in sys.path:
            sys.path.insert(0, candidate)
            inserted.append(candidate)
    try:
        prefix = manifest.entry_module
        for cached in [k for k in sys.modules if k == prefix or k.startswith(f"{prefix}.")]:
            sys.modules.pop(cached, None)
        head = manifest.entry_module.split(".", 1)[0]
        sys.modules.pop(head, None)
        module = importlib.import_module(manifest.entry_module)
    finally:
        for candidate in inserted:
            with contextlib.suppress(ValueError):  # pragma: no cover — defensive
                sys.path.remove(candidate)
    register = getattr(module, "register", None)
    if not callable(register):
        raise ValueError(
            f"plugin {manifest.name!r} entry_module {manifest.entry_module!r} "
            "does not expose a callable `register(ctx)`"
        )
    return register  # type: ignore[return-value]


# ── Public API ──────────────────────────────────────────────
async def discover_plugins(plugin_dir: Path) -> list[LoadedPlugin]:
    """Walk ``plugin_dir`` and return one :class:`LoadedPlugin` per
    discovered folder.

    A folder without a manifest is silently skipped (debug log) so
    admins can stage in-progress plugins without affecting the live
    host. Folders whose manifest fails validation are returned with
    ``error`` set and ``register_func=None`` so the caller can audit
    the failure but never call into broken code.
    """
    discovered: list[LoadedPlugin] = []
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        log.debug("plugin_dir %s does not exist; nothing to discover", plugin_dir)
        return discovered

    for entry in sorted(plugin_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        manifest_path = _find_manifest(entry)
        if manifest_path is None:
            log.debug("plugin folder %s has no manifest; skipping", entry)
            continue
        try:
            text = manifest_path.read_text(encoding="utf-8")
            raw = _parse_manifest_text(text, suffix=manifest_path.suffix.lower())
            manifest = _coerce_manifest(raw)
            sha = _hash_plugin_folder(entry)
            signature = _read_signature(entry, manifest_path)
            register = _resolve_register_callable(entry, manifest)
            discovered.append(
                LoadedPlugin(
                    manifest=manifest,
                    sha256=sha,
                    folder=entry,
                    register_func=register,
                    signature=signature,
                )
            )
        except Exception as exc:
            log.warning(
                "plugin discovery failed for %s: %s", entry.name, exc, exc_info=True
            )
            try:
                sha = _hash_plugin_folder(entry)
            except Exception:  # pragma: no cover - hashing should not fail twice
                sha = ""
            discovered.append(
                LoadedPlugin(
                    manifest=PluginManifest(
                        name=entry.name,
                        version="0.0.0",
                        description="",
                        capability_scopes=(),
                        entry_module="",
                    ),
                    sha256=sha,
                    folder=entry,
                    register_func=None,
                    error=str(exc),
                )
            )
    return discovered


# ── Audit helpers ──────────────────────────────────────────
def _plugin_audit_metadata(plugin: LoadedPlugin) -> dict[str, Any]:
    return {
        "name": plugin.manifest.name,
        "version": plugin.manifest.version,
        "sha256": plugin.sha256,
        "folder": plugin.folder.name,
    }


# Reasons returned by ``evaluate_plugin_for_load`` mapped to the
# stable ``plugin.<reason>`` audit action keys. Keeping the mapping
# in one place lets the loader and the admin reload endpoint share
# the audit schema.
_REASON_TO_AUDIT_ACTION: dict[str, str] = {
    "disabled": "plugin.disabled_by_platform_setting",
    "no_trust_root": "plugin.no_trust_root",
    "signature_missing": "plugin.signature_missing",
    "signature_invalid": "plugin.signature_invalid",
    "not_in_registry": "plugin.not_in_registry",
    "not_approved": "plugin.not_approved",
    "approved": "plugin.signature_verified",
}


async def _sync_registry_row(
    db: AsyncSession,
    plugin: LoadedPlugin,
    *,
    status: PluginRegistryStatus,
    error: str | None = None,
) -> PluginRegistry | None:
    """Upsert a :class:`PluginRegistry` row to mirror the loader's view.

    Status only advances; we never demote an APPROVED row back to
    DISCOVERED, and we never silently flip an already-LOADED row to
    DISCOVERED (a re-discovery of an unchanged sha is the expected
    case on every restart).
    """
    from app.repositories.plugin_registry import PluginRegistryRepository

    repo = PluginRegistryRepository(db)
    row = await repo.get_by_sha(
        name=plugin.manifest.name,
        version=plugin.manifest.version,
        sha256=plugin.sha256,
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    if row is None:
        row = PluginRegistry(
            name=plugin.manifest.name,
            version=plugin.manifest.version,
            sha256=plugin.sha256,
            signature=plugin.signature,
            capability_scopes=list(plugin.manifest.capability_scopes),
            folder_name=plugin.folder.name,
            status=status,
            last_load_attempt_at=now,
            last_load_error=error,
        )
        db.add(row)
        await db.flush()
    else:
        row.signature = plugin.signature
        row.capability_scopes = list(plugin.manifest.capability_scopes)
        row.folder_name = plugin.folder.name
        row.last_load_attempt_at = now
        row.last_load_error = error
        # Status order: DISCOVERED → SIGNED_VERIFIED → APPROVED → LOADED
        # plus REJECTED as a sticky terminal state. Never overwrite
        # APPROVED / REJECTED with DISCOVERED on a re-scan.
        sticky = {PluginRegistryStatus.APPROVED, PluginRegistryStatus.REJECTED}
        if row.status in sticky and status == PluginRegistryStatus.DISCOVERED:
            pass
        elif row.status == PluginRegistryStatus.LOADED and status in (
            PluginRegistryStatus.SIGNED_VERIFIED,
            PluginRegistryStatus.DISCOVERED,
        ):
            pass
        else:
            row.status = status
    plugin.registry_id = str(row.id) if row.id is not None else None
    return row


async def _audit_with_reason(
    db: AsyncSession, plugin: LoadedPlugin, *, reason: str, summary: str
) -> None:
    action = _REASON_TO_AUDIT_ACTION.get(reason, f"plugin.{reason}")
    metadata = _plugin_audit_metadata(plugin)
    metadata["reason"] = reason
    if plugin.registry_id:
        metadata["registry_id"] = plugin.registry_id
    await audit_svc.record(
        db,
        action=action,
        actor_identity_id=None,
        workspace_id=None,
        resource_type="plugin",
        resource_id=None,
        summary=summary,
        metadata=metadata,
    )


async def load_and_register_plugins(
    db: AsyncSession,
    *,
    plugin_dir: Path,
    allow_user_plugins: bool | None = None,
) -> list[LoadedPlugin]:
    """Discover, register, and audit every plugin under ``plugin_dir``.

    ``allow_user_plugins`` semantics:

    * ``False`` — legacy short-circuit. Audit
      ``plugin.disabled_by_platform_setting`` and return immediately.
      No DB read of platform_settings happens. Used by tests that
      want to verify the disabled path without a real platform
      configuration.
    * ``True`` — legacy "load anything that parses". Skips the M3.9
      signing pipeline and the registry approval gate. Used by the
      M2.5.5 test suite and by the in-process recovery path.
    * ``None`` — production M3.9 mode. Reads
      ``platform_settings.plugins`` from the DB; per discovered
      plugin computes sha + reads ``plugin.yaml.sig``; runs
      :func:`app.services.plugin_signing.evaluate_plugin_for_load`;
      only registers when the gate returns ``allowed=True``.
      :class:`PluginRegistry` rows are synced for every discovered
      plugin (DISCOVERED on first sight; LOADED on success;
      REJECTED only via explicit admin action — automatic refusals
      do NOT mark a row REJECTED so an admin's later approval is
      still possible).

    Caller owns the session lifecycle: this function commits its
    own audit + registry writes so partial failures still show up.
    """
    if allow_user_plugins is False:
        log.info(
            "plugin loader: allow_user_plugins=False; skipping discovery in %s",
            plugin_dir,
        )
        await audit_svc.record(
            db,
            action="plugin.disabled_by_platform_setting",
            actor_identity_id=None,
            workspace_id=None,
            resource_type="plugin",
            resource_id=None,
            summary="plugin discovery skipped: allow_user_plugins=False",
            metadata={"plugin_dir": str(plugin_dir)},
        )
        await db.commit()
        return []

    discovered = await discover_plugins(plugin_dir)
    registered: list[LoadedPlugin] = []
    legacy_mode = allow_user_plugins is True

    for plugin in discovered:
        if plugin.error or plugin.register_func is None:
            await audit_svc.record(
                db,
                action="plugin.load_failed",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="plugin",
                resource_id=None,
                summary=f"plugin {plugin.manifest.name!r} failed to load",
                metadata={
                    **_plugin_audit_metadata(plugin),
                    "error": plugin.error or "no register() function",
                },
            )
            continue

        if not legacy_mode:
            await _sync_registry_row(
                db, plugin, status=PluginRegistryStatus.DISCOVERED
            )
            await audit_svc.record(
                db,
                action="plugin.discovered",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="plugin",
                resource_id=None,
                summary=f"plugin {plugin.manifest.name!r} discovered",
                metadata={
                    **_plugin_audit_metadata(plugin),
                    "registry_id": plugin.registry_id,
                    "capability_scopes": list(plugin.manifest.capability_scopes),
                    "signature_present": plugin.signature is not None,
                },
            )

            from app.services.plugin_signing import evaluate_plugin_for_load

            allowed, reason = await evaluate_plugin_for_load(
                db,
                manifest=plugin.manifest,
                sha256=plugin.sha256,
                signature_provided=plugin.signature,
            )
            if not allowed:
                await _audit_with_reason(
                    db,
                    plugin,
                    reason=reason,
                    summary=(
                        f"plugin {plugin.manifest.name!r} skipped "
                        f"(reason={reason})"
                    ),
                )
                continue
            # On the approved path, advance the registry row to
            # ``signed_verified`` (an admin's earlier APPROVED row
            # stays APPROVED — _sync_registry_row's monotonic guard
            # handles that).
            await _sync_registry_row(
                db,
                plugin,
                status=PluginRegistryStatus.SIGNED_VERIFIED,
            )
            await _audit_with_reason(
                db,
                plugin,
                reason="approved",
                summary=(
                    f"plugin {plugin.manifest.name!r} signature verified "
                    "and admin-approved"
                ),
            )

        ctx = PluginContext(manifest=plugin.manifest)
        try:
            plugin.register_func(ctx)
        except Exception as exc:
            log.exception(
                "plugin register() crashed for %s", plugin.manifest.name
            )
            if not legacy_mode:
                await _sync_registry_row(
                    db,
                    plugin,
                    status=PluginRegistryStatus.SIGNED_VERIFIED,
                    error=f"{type(exc).__name__}: {exc}",
                )
            await audit_svc.record(
                db,
                action="plugin.load_failed",
                actor_identity_id=None,
                workspace_id=None,
                resource_type="plugin",
                resource_id=None,
                summary=f"plugin {plugin.manifest.name!r} register() raised",
                metadata={
                    **_plugin_audit_metadata(plugin),
                    "error_class": type(exc).__name__,
                    "error": str(exc),
                },
            )
            continue

        plugin.registered_hooks = ctx.hooks_registered
        plugin.registered_channels = ctx.channels_registered
        plugin.registered_providers = ctx.providers_registered
        registered.append(plugin)

        if not legacy_mode:
            await _sync_registry_row(
                db, plugin, status=PluginRegistryStatus.LOADED
            )

        await audit_svc.record(
            db,
            action="plugin.loaded",
            actor_identity_id=None,
            workspace_id=None,
            resource_type="plugin",
            resource_id=None,
            summary=(
                f"plugin {plugin.manifest.name!r} v{plugin.manifest.version} "
                f"registered {len(ctx.hooks_registered)} hooks / "
                f"{len(ctx.channels_registered)} channel kinds / "
                f"{len(ctx.providers_registered)} provider kinds"
            ),
            metadata={
                **_plugin_audit_metadata(plugin),
                "capability_scopes": list(plugin.manifest.capability_scopes),
                "hooks_registered": list(ctx.hooks_registered),
                "tools_requested": list(ctx.tools_registered),
                "channels_registered": list(ctx.channels_registered),
                "providers_registered": list(ctx.providers_registered),
                "registry_id": plugin.registry_id,
            },
        )
    await db.commit()
    return registered


def list_loaded_plugin_summaries(loaded: Iterable[LoadedPlugin]) -> list[dict[str, Any]]:
    """Compact projection used by docs / debug routes."""
    return [
        {
            "name": p.manifest.name,
            "version": p.manifest.version,
            "sha256": p.sha256,
            "hooks_registered": list(p.registered_hooks),
            "channels_registered": list(p.registered_channels),
            "providers_registered": list(p.registered_providers),
        }
        for p in loaded
    ]


__all__ = [
    "ALL_CAPABILITY_SCOPES",
    "EXTRA_CAPABILITY_SCOPES",
    "LoadedPlugin",
    "PluginContext",
    "PluginManifest",
    "discover_plugins",
    "list_loaded_plugin_summaries",
    "load_and_register_plugins",
]
