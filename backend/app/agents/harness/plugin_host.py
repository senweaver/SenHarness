"""Plugin host — the in-process registry that the native runner fires
its lifecycle callbacks through.

Six hooks make up the public surface:

* ``pre_tool_call`` / ``post_tool_call`` — wrap the runner's
  ``Agent.is_call_tools_node`` branch.
* ``pre_llm_call`` / ``post_llm_call`` — wrap the runner's
  ``Agent.is_model_request_node`` branch.
* ``on_session_start`` / ``on_session_end`` — wrap the chat turn
  boundary in ``_pydantic_ai_stream``.

Every callback runs inside ``asyncio.wait_for(..., timeout=1.0)``: a
slow plugin gets cut off and the run keeps moving. Exceptions and
timeouts both land as ``plugin.hook_failed`` / ``plugin.hook_timeout``
audit rows; the main path is never disturbed. This is the M2.5.5
default-deny + fail-safe contract.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

HookCallback = Callable[..., Awaitable[Any] | Any]

# Whitelist enforced at register time so a typo can never silently
# install a callback under a hook name the runner will never fire.
HOOK_NAMES: frozenset[str] = frozenset(
    {
        "pre_tool_call",
        "post_tool_call",
        "on_session_start",
        "on_session_end",
        "pre_llm_call",
        "post_llm_call",
    }
)

# Per-callback budget. Picked so a chatty plugin can still react to
# every hook in a multi-tool turn without holding up the runner.
HOOK_TIMEOUT_SECONDS: float = 1.0


class PluginHost:
    """In-process registry of hook callbacks.

    The host is intentionally process-local: plugins are loaded once
    at startup by :func:`app.services.plugin_loader.load_and_register_plugins`,
    and there is no remote dispatch path. M3.9 will layer signature
    verification on top of the loader; this class stays untouched.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookCallback]] = {h: [] for h in HOOK_NAMES}

    def register_hook(self, hook_name: str, callback: HookCallback) -> None:
        if hook_name not in HOOK_NAMES:
            raise ValueError(
                f"unknown plugin hook {hook_name!r}; valid={sorted(HOOK_NAMES)}"
            )
        self._hooks[hook_name].append(callback)

    def registered(self, hook_name: str) -> int:
        return len(self._hooks.get(hook_name, ()))

    def clear(self, hook_name: str | None = None) -> None:
        """Drop registered callbacks. Test hook only."""
        if hook_name is None:
            for name in HOOK_NAMES:
                self._hooks[name] = []
            return
        self._hooks[hook_name] = []

    async def fire(self, hook_name: str, /, **payload: Any) -> None:
        """Invoke every callback registered for ``hook_name``.

        Each callback is invoked under a per-call timeout. Failures are
        isolated: one exploding plugin does not block its siblings, and
        the runner's main path is never raised into. Returning ``None``
        is the contract — fire intentionally swallows callback return
        values so a plugin cannot mutate runner state through the hook.
        """
        callbacks = list(self._hooks.get(hook_name, ()))
        if not callbacks:
            return
        for cb in callbacks:
            try:
                await asyncio.wait_for(
                    _call_maybe_async(cb, **payload),
                    timeout=HOOK_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                log.warning(
                    "plugin hook timeout hook=%s callback=%s",
                    hook_name,
                    _callback_label(cb),
                )
                await _safe_audit(
                    "plugin.hook_timeout",
                    metadata={
                        "hook": hook_name,
                        "callback": _callback_label(cb),
                        "timeout_seconds": HOOK_TIMEOUT_SECONDS,
                    },
                )
            except Exception as exc:
                log.exception(
                    "plugin hook failed hook=%s callback=%s",
                    hook_name,
                    _callback_label(cb),
                )
                await _safe_audit(
                    "plugin.hook_failed",
                    metadata={
                        "hook": hook_name,
                        "callback": _callback_label(cb),
                        "error_class": type(exc).__name__,
                    },
                )


async def _call_maybe_async(cb: HookCallback, /, **payload: Any) -> Any:
    """Plugins may register either coroutines or plain functions; we
    bridge both into a single awaitable so :func:`asyncio.wait_for`
    can enforce the budget uniformly.
    """
    result = cb(**payload)
    if inspect.isawaitable(result):
        return await result
    return result


def _callback_label(cb: HookCallback) -> str:
    """Stable identifier for audit / log output. ``__qualname__``
    survives bound methods and decorated functions; the module prefix
    helps operators trace back to the loaded plugin folder.
    """
    module = getattr(cb, "__module__", "?")
    qual = getattr(cb, "__qualname__", None) or getattr(cb, "__name__", "?")
    return f"{module}.{qual}"


async def _safe_audit(action: str, *, metadata: dict[str, Any]) -> None:
    """Wrapper that calls :func:`_audit_async` defensively.

    A test or extension may swap ``_audit_async`` for a stub that
    raises; the runner's invariant ("plugin host never raises into
    the run path") still holds because this wrapper catches anything
    the audit hook might leak.
    """
    try:
        await _audit_async(action, metadata=metadata)
    except Exception:
        log.warning("plugin audit %s wrapper caught exception", action, exc_info=True)


async def _audit_async(action: str, *, metadata: dict[str, Any]) -> None:
    """Open a short-lived DB session and write an audit row.

    The plugin host has no caller-supplied session, so we own the
    connection lifecycle here. Audit failure is itself logged but
    never raised — the M2.5.5 contract is that nothing in this module
    may surface back into the runner.
    """
    try:
        from app.db.session import get_session_factory  # local import — break cycle
        from app.services import audit as audit_svc

        factory = get_session_factory()
        async with factory() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=None,
                workspace_id=None,
                resource_type="plugin",
                resource_id=None,
                summary=action,
                metadata=metadata,
            )
            await db.commit()
    except Exception:
        log.warning("plugin audit %s failed", action, exc_info=True)


# Module-level singleton consumed by the runner. Tests reset it via
# :meth:`PluginHost.clear`.
plugin_host = PluginHost()


# Module-level shims kept so internal callers that pre-date M2.5.5 (and
# external plugins that may have learned the legacy import path) keep
# working. ``register`` was the original surface; the M2.5.5 contract
# adds ``fire`` and ``register_hook`` on the singleton.
def register(hook_name: str, callback: HookCallback) -> None:
    plugin_host.register_hook(hook_name, callback)


async def fire(hook_name: str, /, **payload: Any) -> None:
    await plugin_host.fire(hook_name, **payload)


def clear(hook_name: str | None = None) -> None:
    plugin_host.clear(hook_name)


__all__ = [
    "HOOK_NAMES",
    "HOOK_TIMEOUT_SECONDS",
    "PluginHost",
    "clear",
    "fire",
    "plugin_host",
    "register",
]
