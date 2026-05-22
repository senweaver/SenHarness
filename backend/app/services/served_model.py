"""Two-Model-ID resolver service (M2.5.7).

Decouples the **client-facing** (``served_model_name``) from the
**upstream** routing target so swapping providers does not break
provider-side prompt cache prefixes that key on the model name.

Resolution order
----------------

1. ``agent.served_model_name`` is non-empty → use as ``served_name``.
2. ``fallback_upstream`` (the runner's existing per-turn override or
   the resolver's chosen upstream) → use as ``served_name``.
3. Default to the empty string — caller decides what to substitute
   (downstream callers fall back to the resolver's ``model_name``).

Then the alias map lookup:

* If ``served_name`` is a key in
  ``workspace.home_config_json["providers"]["served_alias_map"]`` →
  ``upstream`` is the mapped value, ``matched_via='workspace_alias'``.
* Otherwise → ``upstream = served_name`` and ``matched_via`` is one
  of ``'agent_field'`` / ``'fallback'`` depending on which step
  produced the ``served_name``.

The runner consults :func:`resolve_served_model` once per turn
(short-lived DB session). The result drives:

* ``RunRequest.policy["served_model_name"]`` — surfaced on
  ``USAGE`` / ``FINAL`` events, ``tool_call_json.model_name`` and
  audit metadata.
* When ``matched_via='workspace_alias'`` and no per-turn
  ``model_override`` exists, the alias map's upstream is forwarded
  to :func:`app.agents.kernels.model_client.parse_override`. Values
  that are not ``provider:model`` shaped fall through to the
  workspace's default provider — see ``docs/extensions-and-governance.md``
  (Provider routing → Two-model-ID pattern).

The :func:`list_served_models_for_workspace` helper backs
``GET /v1/models``; it deduplicates the union of agent-declared
served names and alias-map keys, so the response is stable across
provider swaps.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent
from app.db.models.workspace import Workspace
from app.schemas.served_model import (
    ResolvedServedModel,
    ServedAliasMap,
    ServedModelEntry,
)

log = logging.getLogger(__name__)

__all__ = [
    "PROVIDERS_KEY",
    "SERVED_ALIAS_MAP_KEY",
    "delete_alias",
    "get_alias_map",
    "list_served_models_for_workspace",
    "resolve_served_model",
    "upsert_alias",
]


PROVIDERS_KEY = "providers"
SERVED_ALIAS_MAP_KEY = "served_alias_map"


def _read_alias_map(workspace: Workspace | None) -> dict[str, str]:
    """Return the alias map dict from workspace.home_config_json — never raises."""
    if workspace is None:
        return {}
    home = workspace.home_config_json or {}
    providers = home.get(PROVIDERS_KEY)
    if not isinstance(providers, dict):
        return {}
    raw = providers.get(SERVED_ALIAS_MAP_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if isinstance(k, str) and isinstance(v, str) and k and v:
            out[k] = v
    return out


def _write_alias_map(workspace: Workspace, alias_map: dict[str, str]) -> None:
    """In-place update workspace.home_config_json with the new alias map.

    Caller owns commit. Uses dict copies so SQLAlchemy registers the
    JSONB mutation (in-place edits on dict columns are not auto-detected).
    """
    home = dict(workspace.home_config_json or {})
    providers = dict(home.get(PROVIDERS_KEY) or {})
    if alias_map:
        providers[SERVED_ALIAS_MAP_KEY] = dict(alias_map)
    elif SERVED_ALIAS_MAP_KEY in providers:
        # Drop the key entirely when the map is empty so the JSONB
        # blob doesn't accumulate dead empty maps over time.
        providers.pop(SERVED_ALIAS_MAP_KEY)
    if providers:
        home[PROVIDERS_KEY] = providers
    elif PROVIDERS_KEY in home:
        home.pop(PROVIDERS_KEY)
    workspace.home_config_json = home


async def get_alias_map(db: AsyncSession, *, workspace_id: uuid.UUID) -> dict[str, str]:
    """Read-only accessor used by tests and the REST list route."""
    workspace = await db.get(Workspace, workspace_id)
    return _read_alias_map(workspace)


async def upsert_alias(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    served_name: str,
    upstream: str,
) -> ServedAliasMap:
    """Add / update a single alias entry. Validation runs before write."""
    payload = ServedAliasMap(aliases={served_name: upstream})
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise ValueError("workspace_not_found")
    current = _read_alias_map(workspace)
    cleaned = next(iter(payload.aliases.items()))
    current[cleaned[0]] = cleaned[1]
    _write_alias_map(workspace, current)
    return ServedAliasMap(aliases=current)


async def delete_alias(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    served_name: str,
) -> ServedAliasMap:
    """Remove a single alias entry. Idempotent — missing keys are no-ops."""
    workspace = await db.get(Workspace, workspace_id)
    if workspace is None:
        raise ValueError("workspace_not_found")
    current = _read_alias_map(workspace)
    current.pop(served_name, None)
    _write_alias_map(workspace, current)
    return ServedAliasMap(aliases=current)


async def resolve_served_model(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    agent: Agent | None,
    fallback_upstream: str | None = None,
) -> ResolvedServedModel:
    """Compute (served_name, upstream, matched_via) for a single run.

    Invariants:

    * ``served_name`` is non-empty exactly when at least one of
      ``agent.served_model_name`` / ``fallback_upstream`` is non-empty.
    * When ``served_name`` matches a key in the workspace alias map,
      ``upstream`` is the mapped value (may differ from served);
      otherwise ``upstream == served_name`` and ``matched_via`` is
      ``'agent_field'`` or ``'fallback'``.
    * When neither input gives a name, both fields are the empty
      string and ``matched_via='fallback'`` — the caller (runner)
      treats this as "use the resolver's chosen upstream as both".
    """
    served_field = getattr(agent, "served_model_name", None) if agent is not None else None
    served_field = served_field.strip() if isinstance(served_field, str) else None
    fallback = fallback_upstream.strip() if isinstance(fallback_upstream, str) else None

    if served_field:
        served_name = served_field
        source = "agent_field"
    elif fallback:
        served_name = fallback
        source = "fallback"
    else:
        return ResolvedServedModel(
            served_name="",
            upstream="",
            matched_via="fallback",
        )

    workspace = await db.get(Workspace, workspace_id)
    alias_map = _read_alias_map(workspace)

    mapped = alias_map.get(served_name)
    if mapped:
        return ResolvedServedModel(
            served_name=served_name,
            upstream=mapped,
            matched_via="workspace_alias",
        )
    return ResolvedServedModel(
        served_name=served_name,
        upstream=served_name,
        matched_via=source,  # type: ignore[arg-type]
    )


async def _list_distinct_agent_served_names(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> list[tuple[str, uuid.UUID]]:
    """Return [(served_name, agent_id), ...] for non-NULL distinct values.

    Picks one ``agent_id`` per served_name (the oldest) so the
    ``/v1/models`` payload can hint the operator at one example owner;
    multiple agents may share a served_name and that's fine.
    """
    stmt = (
        select(Agent.served_model_name, Agent.id)
        .where(
            Agent.workspace_id == workspace_id,
            Agent.served_model_name.is_not(None),
            Agent.deleted_at.is_(None),
        )
        .order_by(Agent.served_model_name.asc(), Agent.created_at.asc())
    )
    rows = (await db.execute(stmt)).all()
    seen: dict[str, uuid.UUID] = {}
    for raw_name, raw_id in rows:
        name = (raw_name or "").strip()
        if not name or name in seen:
            continue
        seen[name] = raw_id
    return [(name, agent_id) for name, agent_id in seen.items()]


async def list_served_models_for_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> list[ServedModelEntry]:
    """Union of agent-declared served names and alias map keys, deduped.

    Sort order is stable (alphabetical) so ``/v1/models`` responses
    are reproducible — the OpenAI client caches the list and a
    flapping order would confuse downstream selection logic.
    """
    workspace = await db.get(Workspace, workspace_id)
    alias_map = _read_alias_map(workspace)
    agent_pairs = await _list_distinct_agent_served_names(db, workspace_id=workspace_id)

    by_name: dict[str, ServedModelEntry] = {}
    for served_name, upstream in alias_map.items():
        by_name[served_name] = ServedModelEntry(
            served_name=served_name,
            upstream=upstream,
            matched_via="workspace_alias",
        )
    for served_name, agent_id in agent_pairs:
        if served_name in by_name:
            by_name[served_name].agent_id = agent_id
            continue
        by_name[served_name] = ServedModelEntry(
            served_name=served_name,
            upstream=served_name,
            matched_via="agent_field",
            agent_id=agent_id,
        )
    return [by_name[name] for name in sorted(by_name.keys())]


def served_names_only(entries: Iterable[ServedModelEntry]) -> list[str]:
    """Helper for callers that only need the list of names."""
    return [e.served_name for e in entries]
