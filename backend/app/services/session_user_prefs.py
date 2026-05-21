"""Per-user chat preferences (model picks, future per-agent overrides).

The data lives on ``Identity.profile_json`` so it travels with the global
identity record (cross-workspace) — same as locale + interests. Workspace-
scoped rules ride on top of this via ``Membership`` / agent ``policy``; this
module only handles **caller-level** preferences.

Shape (under ``profile_json``)::

    {
      "chat_model_prefs": {
        "<agent_id_uuid>": "<provider>:<model>",   # e.g. "deepseek:deepseek-chat"
        "default": "<provider>:<model>"            # used when no agent-specific entry
      }
    }

Reads tolerate missing keys; writes preserve every other ``profile_json``
field (we never blow away the blob, only patch ``chat_model_prefs``).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

log = logging.getLogger(__name__)

PREFS_KEY = "chat_model_prefs"
DEFAULT_KEY = "default"


def _coerce_pref(value: Any) -> str | None:
    """Accept only ``provider:model`` strings; anything else is dropped.

    The frontend forwards this verbatim into ``RunRequest.model_override``,
    which the kernel parses with ``parse_override``. Garbage in here would
    silently disable the override, so we sanitize at write time.
    """
    if not isinstance(value, str):
        return None
    s = value.strip()
    if ":" not in s:
        return None
    if len(s) > 256:
        return None
    return s


async def get_model_pref(
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    agent_id: uuid.UUID | None,
) -> str | None:
    """Return the saved ``provider:model`` for this caller + agent, or None.

    Resolution order:
      1. ``profile_json.chat_model_prefs[<agent_id>]``
      2. ``profile_json.chat_model_prefs.default``
      3. ``None`` — caller falls back to env / workspace provider default.

    ``workspace_id`` is unused today (prefs are global per identity) but kept
    in the signature so future workspace-scoped routing can land without an
    API change.
    """
    _ = workspace_id  # reserved for future workspace-scoped overrides
    try:
        from app.db.session import get_session_factory
        from app.repositories.identity import IdentityRepository
    except ImportError:  # pragma: no cover
        return None

    factory = get_session_factory()
    async with factory() as db:
        identity = await IdentityRepository(db).get(identity_id)
        if identity is None:
            return None
        prefs = (identity.profile_json or {}).get(PREFS_KEY) or {}
        if not isinstance(prefs, dict):
            return None
        if agent_id is not None:
            v = _coerce_pref(prefs.get(str(agent_id)))
            if v is not None:
                return v
        return _coerce_pref(prefs.get(DEFAULT_KEY))


async def set_model_pref(
    *,
    identity_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    model: str | None,
) -> dict[str, str]:
    """Persist (or clear) the caller's preferred model for ``agent_id``.

    Passing ``model=None`` removes the entry — useful for a "reset to default"
    UI affordance. Passing ``agent_id=None`` writes the global default that
    applies whenever no agent-specific entry is set.

    Returns the **full** ``chat_model_prefs`` dict after the update so the
    caller can render the updated state without a re-fetch.
    """
    from app.db.session import get_session_factory
    from app.repositories.identity import IdentityRepository

    factory = get_session_factory()
    async with factory() as db:
        identity = await IdentityRepository(db).get(identity_id)
        if identity is None:
            return {}

        profile = dict(identity.profile_json or {})
        prefs_raw = profile.get(PREFS_KEY)
        prefs: dict[str, str] = (
            {str(k): str(v) for k, v in prefs_raw.items() if isinstance(v, str)}
            if isinstance(prefs_raw, dict)
            else {}
        )

        key = str(agent_id) if agent_id is not None else DEFAULT_KEY
        coerced = _coerce_pref(model) if model is not None else None
        if coerced is None:
            prefs.pop(key, None)
        else:
            prefs[key] = coerced

        profile[PREFS_KEY] = prefs
        await IdentityRepository(db).update(identity, profile_json=profile)
        await db.commit()
        return prefs


async def list_model_prefs(*, identity_id: uuid.UUID) -> dict[str, str]:
    """Return every ``chat_model_prefs`` entry for the caller (read-only)."""
    from app.db.session import get_session_factory
    from app.repositories.identity import IdentityRepository

    factory = get_session_factory()
    async with factory() as db:
        identity = await IdentityRepository(db).get(identity_id)
        if identity is None:
            return {}
        prefs = (identity.profile_json or {}).get(PREFS_KEY) or {}
        if not isinstance(prefs, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in prefs.items():
            cv = _coerce_pref(v)
            if cv is not None:
                out[str(k)] = cv
        return out
