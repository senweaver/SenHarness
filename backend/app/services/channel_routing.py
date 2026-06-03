"""Channel → multi-agent routing core (P0).

This is the brain of the "single channel → many agents" feature. Given an
inbound IM message and a channel's ``routing_config_json`` it:

1. derives ``peer_key`` (conversation) + ``sender_key`` (individual user);
2. resolves the sender to a SenHarness identity via ``channel_user_link``;
3. picks the candidate **pool** by ``bind_scope``
   (agent = single · workspace = visible agents in a workspace ·
   user = agents the identity can reach across their workspaces),
   optionally narrowed by ``allowlist_agent_ids``;
4. decides the target inside the pool — **command > conversation
   stickiness > default** — honouring the numbered-selection window;
5. enforces the DM/group policy gate and the sender allowlist;
6. returns a :class:`RouteOutcome` telling the dispatcher whether to send
   a direct presenter reply (command / welcome / switch / policy) or to
   run a specific ``(workspace, agent)`` as the resolved identity.

The routed target is always ∈ the resolved pool, which is exactly the
visibility / cross-workspace legality boundary — so routing can never
escalate a caller into an agent they couldn't otherwise reach.

``bind_scope == "agent"`` never reaches here; the dispatcher short-circuits
that to the legacy path for full backward compatibility.

The service never commits — the dispatcher owns the transaction.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent, AgentVisibility
from app.db.models.channel import Channel
from app.db.models.channel_link import ChannelBinding, ChannelConversationState
from app.db.models.squad import Squad
from app.repositories.agent import AgentRepository
from app.repositories.channel_link import (
    ChannelBindingRepository,
    ChannelConversationStateRepository,
    ChannelUserLinkRepository,
)
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import WorkspaceRepository
from app.services import audit as audit_svc
from app.services import squad_runtime
from app.services import workspace as ws_svc
from app.services.channels import _commands, _copy, _handoff
from app.services.channels._handoff import HandoffRule
from app.services.channels._peer_key import (
    derive_peer_key,
    derive_sender_key,
    is_group_conversation,
)
from app.services.channels._sender_filter import is_sender_allowed
from app.services.channels.base import InboundMessage

log = logging.getLogger(__name__)

# Pools larger than this are capped in numbered menus (Top N); ``/agents``
# can still page beyond it in P1.
_MENU_TOP_N = 9
_VALID_SCOPES = frozenset({"agent", "workspace", "user", "squad"})
_VALID_POLICIES = frozenset({"open", "allowlist", "disabled", "pairing"})
_VALID_MENU_STYLES = frozenset({"auto", "text", "buttons"})
_VALID_ATTRIBUTION = frozenset({"prefix", "identity", "off"})
_VALID_GROUP_OVERRIDE = frozenset({"shared", "per_sender"})

# Binding specificity — higher rung wins. Only ``peer`` / ``group`` /
# ``channel_default`` are actively matched today; the rest are reserved
# rungs of the OpenClaw-style ladder so the table can grow without code
# churn. Unknown scopes sort below ``channel_default`` (never win).
_BINDING_SPECIFICITY: dict[str, int] = {
    "peer": 70,
    "thread": 60,
    "role": 50,
    "guild": 40,
    "team": 30,
    "account": 20,
    "group": 10,
    "channel_default": 0,
}

# Redis namespace for the ``/bind`` one-time codes.
_BIND_CODE_PREFIX = "channel:bind:"
_BIND_CODE_TTL_SECONDS = 600


# ─── Routing config ──────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RoutingConfig:
    bind_scope: str = "agent"
    scope_ref_id: uuid.UUID | None = None
    allowlist_agent_ids: tuple[uuid.UUID, ...] | None = None
    dm_policy: str = "open"
    group_policy: str = "disabled"
    menu_style: str = "auto"
    selection_window_seconds: int = 300
    reply_attribution: str = "prefix"
    # P1 — group stickiness sharing. ``shared`` (P0) keeps one route per
    # group ``chat_id``; ``per_sender`` lets each sender override the group
    # route for their own messages while still falling back to it.
    group_override: str = "shared"
    # P1 — deterministic natural-language handoff rules (keyword router).
    handoff_rules: tuple[HandoffRule, ...] = ()


def _coerce_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return uuid.UUID(value.strip())
        except ValueError:
            return None
    return None


def parse_routing_config(raw: dict | None) -> RoutingConfig:
    """Parse a stored ``routing_config_json`` blob into a typed config.

    Unknown / malformed values fall back to the safe default so a hand-
    edited row can never crash the dispatcher.
    """
    raw = raw or {}
    scope = str(raw.get("bind_scope") or "agent").strip().lower()
    if scope not in _VALID_SCOPES:
        scope = "agent"

    allow_raw = raw.get("allowlist_agent_ids")
    allowlist: tuple[uuid.UUID, ...] | None = None
    if isinstance(allow_raw, (list, tuple)):
        parsed = [u for u in (_coerce_uuid(x) for x in allow_raw) if u is not None]
        allowlist = tuple(parsed) if parsed else None

    dm_policy = str(raw.get("dm_policy") or "open").strip().lower()
    if dm_policy not in _VALID_POLICIES:
        dm_policy = "open"
    group_policy = str(raw.get("group_policy") or "disabled").strip().lower()
    if group_policy not in _VALID_POLICIES:
        group_policy = "disabled"
    menu_style = str(raw.get("menu_style") or "auto").strip().lower()
    if menu_style not in _VALID_MENU_STYLES:
        menu_style = "auto"
    attribution = str(raw.get("reply_attribution") or "prefix").strip().lower()
    if attribution not in _VALID_ATTRIBUTION:
        attribution = "prefix"
    group_override = str(raw.get("group_override") or "shared").strip().lower()
    if group_override not in _VALID_GROUP_OVERRIDE:
        group_override = "shared"
    try:
        window = int(raw.get("selection_window_seconds", 300))
    except (TypeError, ValueError):
        window = 300
    window = max(0, window)

    return RoutingConfig(
        bind_scope=scope,
        scope_ref_id=_coerce_uuid(raw.get("scope_ref_id")),
        allowlist_agent_ids=allowlist,
        dm_policy=dm_policy,
        group_policy=group_policy,
        menu_style=menu_style,
        selection_window_seconds=window,
        reply_attribution=attribution,
        group_override=group_override,
        handoff_rules=_handoff.parse_handoff_rules(raw.get("handoff_rules")),
    )


def normalize_routing_config(raw: dict | None) -> dict:
    """Validate + canonicalize a config blob for storage.

    Keeps the JSON column tidy (no stray keys, canonical casing) while
    rejecting nothing destructively — parse_routing_config already
    fail-safes, this just produces the persisted shape.
    """
    cfg = parse_routing_config(raw)
    return {
        "bind_scope": cfg.bind_scope,
        "scope_ref_id": str(cfg.scope_ref_id) if cfg.scope_ref_id else None,
        "allowlist_agent_ids": (
            [str(a) for a in cfg.allowlist_agent_ids] if cfg.allowlist_agent_ids else None
        ),
        "dm_policy": cfg.dm_policy,
        "group_policy": cfg.group_policy,
        "menu_style": cfg.menu_style,
        "selection_window_seconds": cfg.selection_window_seconds,
        "reply_attribution": cfg.reply_attribution,
        "group_override": cfg.group_override,
        "handoff_rules": _handoff.dump_handoff_rules(cfg.handoff_rules),
    }


# ─── Layered binding ("most-specific-wins") ──────────────────
@dataclass(frozen=True, slots=True)
class BindingOverride:
    """The winning binding's effective overrides (null = inherit)."""

    bind_scope: str | None = None
    scope_ref_id: uuid.UUID | None = None
    allowlist_agent_ids: tuple[uuid.UUID, ...] | None = None
    target_agent_id: uuid.UUID | None = None


def _binding_applies(
    binding: ChannelBinding, *, peer_key: str, group_key: str, is_group: bool
) -> bool:
    scope = binding.match_scope
    if scope == "channel_default":
        return True
    value = (binding.match_value or "").strip()
    if not value:
        return False
    if scope in ("peer", "thread", "role"):
        return value == peer_key
    if scope in ("group", "guild", "team", "account"):
        return is_group and value == group_key
    return False


async def resolve_binding(
    db: AsyncSession,
    *,
    channel: Channel,
    peer_key: str,
    group_key: str,
    is_group: bool,
) -> BindingOverride | None:
    """Pick the most-specific matching binding for this inbound.

    Returns ``None`` when the channel has no binding rows (the common
    case) so the caller falls back to the channel-level config — making
    an empty table exactly the P0 behaviour. Ties at equal specificity
    break on ``priority`` (higher wins), then newest ``created_at``.
    """
    rows = await ChannelBindingRepository(db).list_for_channel(channel_id=channel.id)
    if not rows:
        return None
    matching = [
        b
        for b in rows
        if _binding_applies(b, peer_key=peer_key, group_key=group_key, is_group=is_group)
    ]
    if not matching:
        return None

    def _rank(b: ChannelBinding) -> tuple[int, int, datetime]:
        return (
            _BINDING_SPECIFICITY.get(b.match_scope, -1),
            b.priority or 0,
            b.created_at or datetime.min,
        )

    winner = max(matching, key=_rank)
    bind_scope = (winner.bind_scope or "").strip().lower() or None
    if bind_scope is not None and bind_scope not in _VALID_SCOPES:
        bind_scope = None
    allow_raw = winner.allowlist_agent_ids_json
    allowlist: tuple[uuid.UUID, ...] | None = None
    if isinstance(allow_raw, (list, tuple)):
        parsed = [u for u in (_coerce_uuid(x) for x in allow_raw) if u is not None]
        allowlist = tuple(parsed) if parsed else None
    return BindingOverride(
        bind_scope=bind_scope,
        scope_ref_id=winner.scope_ref_id,
        allowlist_agent_ids=allowlist,
        target_agent_id=winner.target_agent_id,
    )


def _apply_binding(routing: RoutingConfig, override: BindingOverride | None) -> RoutingConfig:
    if override is None:
        return routing
    return replace(
        routing,
        bind_scope=override.bind_scope or routing.bind_scope,
        scope_ref_id=override.scope_ref_id or routing.scope_ref_id,
        allowlist_agent_ids=(
            override.allowlist_agent_ids
            if override.allowlist_agent_ids is not None
            else routing.allowlist_agent_ids
        ),
    )


# ─── Outcome ─────────────────────────────────────────────────
@dataclass(slots=True)
class RouteOutcome:
    """What the dispatcher should do with one inbound message."""

    # "direct" — send ``reply_text`` straight back (command / welcome /
    # switch / policy), do NOT run an agent.
    # "run" — run ``(target_workspace_id, target_agent_id)`` as
    # ``identity_id``; the dispatcher presents the reply afterwards.
    # "drop" — silently stop (sender blocked).
    action: str
    reply_text: str | None = None

    target_workspace_id: uuid.UUID | None = None
    target_agent_id: uuid.UUID | None = None
    identity_id: uuid.UUID | None = None
    agent_name: str | None = None
    team_name: str | None = None
    user_text: str | None = None
    switched: bool = False
    show_footer: bool = False

    peer_key: str = ""
    sender_key: str = ""
    lang: str = _copy.DEFAULT_LANG
    attribution: str = "prefix"

    # Rich-channel hints (P1). For a "direct" menu reply (welcome / agents
    # list) ``menu_options`` carries the ordered ``[(index, name, desc)]``
    # so the dispatcher can render quick-reply buttons on capable channels;
    # plain-text channels just send ``reply_text``. ``team_name`` (above)
    # feeds the presenter's ``【team › agent】`` attribution for a squad.
    menu_options: list[tuple[int, str, str | None]] | None = None
    menu_style: str = "auto"


# ─── Identity resolution + binding ───────────────────────────
async def resolve_identity(
    db: AsyncSession, *, channel: Channel, external_user_id: str
) -> uuid.UUID | None:
    link = await ChannelUserLinkRepository(db).get_by_channel_user(
        channel_id=channel.id, external_user_id=external_user_id
    )
    return link.identity_id if link is not None else None


async def link_identity(
    db: AsyncSession,
    *,
    channel: Channel,
    external_user_id: str,
    identity_id: uuid.UUID,
    verified_via: str,
    created_by: uuid.UUID | None = None,
) -> None:
    """Create or refresh a ``channel_user_link`` (idempotent on the pair)."""
    repo = ChannelUserLinkRepository(db)
    existing = await repo.get_by_channel_user(
        channel_id=channel.id, external_user_id=external_user_id
    )
    now = _utcnow()
    if existing is not None:
        await repo.update(
            existing,
            identity_id=identity_id,
            verified_at=now,
            verified_via=verified_via,
        )
    else:
        await repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.id,
            external_user_id=external_user_id,
            identity_id=identity_id,
            verified_at=now,
            verified_via=verified_via,
            created_by=created_by,
        )
    await audit_svc.record(
        db,
        action="channel.route.identity_linked",
        actor_identity_id=identity_id,
        workspace_id=channel.workspace_id,
        resource_type="channel",
        resource_id=channel.id,
        summary=f"identity linked via {verified_via}",
        metadata={
            "channel_id": str(channel.id),
            "external_user_hash": _redact(external_user_id),
            "verified_via": verified_via,
        },
    )


async def mint_bind_code(db: AsyncSession, *, channel: Channel, identity_id: uuid.UUID) -> dict:
    """Issue a one-time ``/bind`` code (Redis, short TTL).

    The code maps to ``identity_id`` so whoever enters it in chat gets
    linked to that identity. Returns ``{code, ttl_seconds}``.
    """
    from app.core.rate_limit import get_redis

    code = f"{uuid.uuid4().int % 1_000_000:06d}"
    r = get_redis()
    await r.set(_bind_key(channel.id, code), str(identity_id), ex=_BIND_CODE_TTL_SECONDS)
    await audit_svc.record(
        db,
        action="channel.route.bind_code_issued",
        actor_identity_id=identity_id,
        workspace_id=channel.workspace_id,
        resource_type="channel",
        resource_id=channel.id,
        summary="bind code issued",
        metadata={"channel_id": str(channel.id), "ttl_seconds": _BIND_CODE_TTL_SECONDS},
    )
    return {"code": code, "ttl_seconds": _BIND_CODE_TTL_SECONDS}


async def _consume_bind_code(
    db: AsyncSession, *, channel: Channel, code: str, external_user_id: str
) -> uuid.UUID | None:
    from app.core.rate_limit import get_redis

    code = (code or "").strip()
    if not (code.isdigit() and len(code) == 6):
        return None
    r = get_redis()
    raw = await r.get(_bind_key(channel.id, code))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    identity_id = _coerce_uuid(raw)
    if identity_id is None:
        return None
    await link_identity(
        db,
        channel=channel,
        external_user_id=external_user_id,
        identity_id=identity_id,
        verified_via="bind_code",
        created_by=identity_id,
    )
    try:
        await r.delete(_bind_key(channel.id, code))
    except Exception:  # pragma: no cover - best effort
        pass
    return identity_id


def _bind_key(channel_id: uuid.UUID, code: str) -> str:
    return f"{_BIND_CODE_PREFIX}{channel_id}:{code}"


# ─── Pool resolution ─────────────────────────────────────────
def _agent_accessible(agent: Agent, identity_id: uuid.UUID | None) -> bool:
    visibility = str(getattr(agent, "visibility", AgentVisibility.WORKSPACE))
    if visibility in (AgentVisibility.WORKSPACE.value, AgentVisibility.PUBLIC.value):
        return True
    if visibility == AgentVisibility.PRIVATE.value:
        return identity_id is not None and agent.created_by == identity_id
    return True


async def _visible_agents_in_workspace(
    db: AsyncSession, *, workspace_id: uuid.UUID, identity_id: uuid.UUID | None
) -> list[Agent]:
    rows = await AgentRepository(db).list_visible(
        workspace_id=workspace_id, identity_id=identity_id, limit=200
    )
    return [a for a in rows if _agent_accessible(a, identity_id)]


async def resolve_pool(
    db: AsyncSession,
    *,
    channel: Channel,
    routing: RoutingConfig,
    identity_id: uuid.UUID | None,
    default_agent_id: uuid.UUID | None = None,
    squad: Squad | None = None,
) -> list[Agent]:
    """Candidate agents for this channel + scope, allowlist-narrowed.

    ``default_agent_id`` defaults to ``channel.default_agent_id`` but a
    layered binding can supply its own (the binding's ``target_agent_id``)
    so the unbound-user / agent fallbacks honour the matched rule.

    For ``bind_scope=squad`` the pool is the squad's accessible member
    agents (``squad`` is pre-resolved by :func:`resolve_route`); a missing /
    cross-workspace squad yields an empty pool (no escalation).
    """
    if default_agent_id is None:
        default_agent_id = channel.default_agent_id
    pool: list[Agent] = []
    if routing.bind_scope == "squad":
        if squad is None:
            squad = await squad_runtime.resolve_squad(
                db, squad_id=routing.scope_ref_id, workspace_id=channel.workspace_id
            )
        if squad is not None:
            pool = await squad_runtime.resolve_squad_pool(db, squad=squad, identity_id=identity_id)
    elif routing.bind_scope == "workspace":
        ws_id = routing.scope_ref_id or channel.workspace_id
        pool = await _visible_agents_in_workspace(db, workspace_id=ws_id, identity_id=identity_id)
    elif routing.bind_scope == "user":
        if identity_id is None:
            # Unbound user on a user-scoped channel can only reach the
            # channel's own default agent (if any) until they /bind.
            pool = await _default_only_pool(
                db, default_agent_id=default_agent_id, identity_id=identity_id
            )
        else:
            seen: set[uuid.UUID] = set()
            ws_ids = await ws_svc.list_active_workspace_ids_for_identity(
                db, identity_id=identity_id
            )
            for ws_id in ws_ids:
                for a in await _visible_agents_in_workspace(
                    db, workspace_id=ws_id, identity_id=identity_id
                ):
                    if a.id not in seen:
                        seen.add(a.id)
                        pool.append(a)
    else:  # "agent" never reaches the multi path, but stay safe.
        pool = await _default_only_pool(
            db, default_agent_id=default_agent_id, identity_id=identity_id
        )

    if routing.allowlist_agent_ids:
        allow = set(routing.allowlist_agent_ids)
        pool = [a for a in pool if a.id in allow]
    return pool


async def _default_only_pool(
    db: AsyncSession, *, default_agent_id: uuid.UUID | None, identity_id: uuid.UUID | None
) -> list[Agent]:
    if default_agent_id is None:
        return []
    agent = await AgentRepository(db).get(default_agent_id)
    if agent is None or agent.deleted_at is not None:
        return []
    return [agent]


def _order_pool(
    pool: list[Agent], default_agent_id: uuid.UUID | None, *, preserve_order: bool = False
) -> list[Agent]:
    """Canonical menu ordering: default agent first, then by name.

    ``preserve_order`` keeps the incoming order for the non-default rest
    instead of sorting alphabetically — used by squad scope so the menu
    reflects the squad's member-weight ordering.
    """
    default_first = [a for a in pool if a.id == default_agent_id]
    rest_iter = (a for a in pool if a.id != default_agent_id)
    rest = (
        list(rest_iter)
        if preserve_order
        else sorted(rest_iter, key=lambda a: (a.name or "").lower())
    )
    return default_first + rest


# ─── Conversation state ──────────────────────────────────────
async def _get_state(
    db: AsyncSession, *, channel: Channel, peer_key: str, sender_key: str = ""
) -> ChannelConversationState | None:
    return await ChannelConversationStateRepository(db).get_by_channel_peer(
        channel_id=channel.id, peer_key=peer_key, sender_key=sender_key
    )


async def _upsert_state(
    db: AsyncSession,
    *,
    channel: Channel,
    peer_key: str,
    sender_key: str = "",
    active_agent: Agent | None = None,
    menu_options: list[dict] | None = None,
    clear_active: bool = False,
) -> ChannelConversationState:
    repo = ChannelConversationStateRepository(db)
    state = await repo.get_by_channel_peer(
        channel_id=channel.id, peer_key=peer_key, sender_key=sender_key
    )
    now = _utcnow()
    patch: dict = {}
    if clear_active:
        patch["active_agent_id"] = None
        patch["active_workspace_id"] = None
    elif active_agent is not None:
        patch["active_agent_id"] = active_agent.id
        patch["active_workspace_id"] = active_agent.workspace_id
    if menu_options is not None:
        patch["last_menu_options_json"] = menu_options
        patch["last_menu_at"] = now

    if state is None:
        return await repo.create(
            workspace_id=channel.workspace_id,
            channel_id=channel.id,
            peer_key=peer_key,
            sender_key=sender_key,
            last_menu_options_json=menu_options or [],
            active_agent_id=patch.get("active_agent_id"),
            active_workspace_id=patch.get("active_workspace_id"),
            last_menu_at=patch.get("last_menu_at"),
        )
    if patch:
        await repo.update(state, **patch)
    return state


def _within_selection_window(state: ChannelConversationState | None, window_seconds: int) -> bool:
    if state is None or state.last_menu_at is None or window_seconds <= 0:
        return False
    return (_utcnow() - state.last_menu_at) <= timedelta(seconds=window_seconds)


# ─── Main resolver ───────────────────────────────────────────
async def resolve_route(
    db: AsyncSession,
    *,
    channel: Channel,
    inbound: InboundMessage,
    routing: RoutingConfig,
) -> RouteOutcome:
    sender_key = derive_sender_key(inbound)
    peer_key = derive_peer_key(kind=channel.kind, inbound=inbound)
    is_group = is_group_conversation(kind=channel.kind, inbound=inbound)
    group_key = peer_key if is_group else ""

    identity_id = await resolve_identity(db, channel=channel, external_user_id=sender_key)
    lang = await _resolve_lang(db, channel=channel, identity_id=identity_id)
    term = await _resolve_agent_term(db, workspace_id=channel.workspace_id)

    base = RouteOutcome(
        action="drop",
        peer_key=peer_key,
        sender_key=sender_key,
        identity_id=identity_id,
        lang=lang,
        attribution=routing.reply_attribution,
        menu_style=routing.menu_style,
    )

    # Global sender allowlist gate (parity with the legacy path).
    if not is_sender_allowed(channel.sender_allowlist_json or {}, sender_key):
        await _audit(db, channel, "channel.route.sender_blocked", identity_id, sender_key)
        base.action = "drop"
        return base

    cmd = _commands.parse_command(inbound.user_text)

    # ``/bind`` is reachable regardless of policy so users can self-serve.
    if cmd is not None and cmd.code == _commands.CMD_BIND:
        if not cmd.arg:
            return _direct(base, _copy.not_bound(lang=lang))
        bound = await _consume_bind_code(
            db, channel=channel, code=cmd.arg, external_user_id=sender_key
        )
        if bound is None:
            return _direct(base, _copy.bind_failed(lang=lang))
        base.identity_id = bound
        return _direct(base, _copy.bind_ok(lang=lang))

    # DM / group policy gate (always channel-level — bindings narrow the
    # pool, never the access policy).
    policy = routing.group_policy if is_group else routing.dm_policy
    blocked_reply = _policy_block(
        policy, identity_id=identity_id, channel=channel, sender=sender_key
    )
    if blocked_reply is not None:
        await _audit(
            db,
            channel,
            "channel.route.blocked",
            identity_id,
            sender_key,
            extra={"policy": policy, "is_group": is_group},
        )
        return _direct(base, blocked_reply(lang=lang))

    # ── Layered binding: most-specific-wins, empty table ⇒ P0 ──
    override = await resolve_binding(
        db, channel=channel, peer_key=peer_key, group_key=group_key, is_group=is_group
    )
    effective_routing = _apply_binding(routing, override)
    eff_default_agent_id = (
        override.target_agent_id
        if override is not None and override.target_agent_id is not None
        else channel.default_agent_id
    )

    # ── Squad scope (P2): resolve the team + tag the outcome for the
    # presenter's ``【team › member】`` attribution. A missing / cross-
    # workspace squad → not-open (never escalates into another team).
    squad = None
    if effective_routing.bind_scope == "squad":
        squad = await squad_runtime.resolve_squad(
            db, squad_id=effective_routing.scope_ref_id, workspace_id=channel.workspace_id
        )
        if squad is None:
            return _direct(base, _copy.not_open(lang=lang))
        base.team_name = squad.name

    pool = await resolve_pool(
        db,
        channel=channel,
        routing=effective_routing,
        identity_id=identity_id,
        default_agent_id=eff_default_agent_id,
        squad=squad,
    )
    if not pool:
        return _direct(base, _copy.not_open(lang=lang))

    # For a squad, the "default" rung is the squad's default member
    # (configured > channel default > highest-weight), and the menu keeps
    # the member-weight ordering instead of re-sorting alphabetically.
    if squad is not None:
        router_cfg = squad_runtime.parse_router_config(squad.config_json)
        dm = squad_runtime.default_member(
            pool, router_cfg, channel_default_agent_id=channel.default_agent_id
        )
        if dm is not None:
            eff_default_agent_id = dm.id

    ordered = _order_pool(pool, eff_default_agent_id, preserve_order=squad is not None)
    pool_ids = {a.id for a in pool}

    # P1 per-sender override: in a group with ``group_override=per_sender``
    # a sender writes/reads their own state row; otherwise everything keys
    # on the shared ``sender_key=""`` row (the P0 behaviour).
    per_sender = effective_routing.group_override == "per_sender" and is_group
    write_sender_key = sender_key if per_sender else ""
    state = await _get_state(db, channel=channel, peer_key=peer_key, sender_key=write_sender_key)
    group_state = (
        state
        if not per_sender
        else await _get_state(db, channel=channel, peer_key=peer_key, sender_key="")
    )
    current_agent = _effective_sticky_target(
        state, group_state, ordered, pool_ids, eff_default_agent_id
    )

    menu_options = _build_menu_options(ordered)

    # ── Command handling ──
    if cmd is not None:
        outcome = await _handle_command(
            db,
            base=base,
            channel=channel,
            routing=effective_routing,
            cmd=cmd,
            ordered=ordered,
            sender_key=write_sender_key,
            current_agent=current_agent,
            menu_options=menu_options,
            term=term,
            identity_id=identity_id,
        )
        if outcome is not None:
            return outcome
        # ``None`` from a mention-with-text means "run the selected agent";
        # _handle_command set base accordingly.
        if base.action == "run":
            await _audit(
                db,
                channel,
                "channel.route.run",
                identity_id,
                sender_key,
                extra={"agent_id": str(base.target_agent_id), "switched": base.switched},
            )
            return base

    # ── Numbered selection (bare digit within the window) ──
    if cmd is None:
        digit = (inbound.user_text or "").strip()
        if digit.isdigit() and _within_selection_window(
            state, effective_routing.selection_window_seconds
        ):
            selected = _match_menu_number(state, digit, pool_ids)
            if selected is not None:
                agent = next((a for a in ordered if a.id == selected), None)
                if agent is not None:
                    await _upsert_state(
                        db,
                        channel=channel,
                        peer_key=peer_key,
                        sender_key=write_sender_key,
                        active_agent=agent,
                    )
                    await _audit(
                        db,
                        channel,
                        "channel.route.switch",
                        identity_id,
                        sender_key,
                        extra={"agent_id": str(agent.id), "via": "number"},
                    )
                    return _direct(
                        base, _copy.switch_receipt(lang=lang, name=agent.name, team=base.team_name)
                    )

    # ── Natural-language handoff (deterministic keyword router) ──
    if cmd is None:
        handoff = _handoff.match_handoff(inbound.user_text, effective_routing.handoff_rules)
        if handoff is not None:
            agent = _select_agent(handoff.target, ordered)
            if agent is not None and agent.id in pool_ids:
                if handoff.mode == "suggest":
                    # Proactive proposal — let the user accept via number /
                    # @alias. The main agent keeps the current turn.
                    await _upsert_state(
                        db,
                        channel=channel,
                        peer_key=peer_key,
                        sender_key=write_sender_key,
                        menu_options=menu_options,
                    )
                    await _audit(
                        db,
                        channel,
                        "channel.route.handoff_suggested",
                        identity_id,
                        sender_key,
                        extra={"agent_id": str(agent.id)},
                    )
                    return _direct(base, _copy.handoff_offer(lang=lang, name=agent.name))
                # mode == "switch": deterministic handoff — switch + forward.
                await _upsert_state(
                    db,
                    channel=channel,
                    peer_key=peer_key,
                    sender_key=write_sender_key,
                    active_agent=agent,
                )
                await _audit(
                    db,
                    channel,
                    "channel.route.handoff",
                    identity_id,
                    sender_key,
                    extra={"agent_id": str(agent.id), "via": "keyword"},
                )
                base.action = "run"
                base.target_agent_id = agent.id
                base.target_workspace_id = agent.workspace_id
                base.agent_name = agent.name
                base.user_text = inbound.user_text
                base.switched = True
                return base

    # ── Squad scope: explicit (above) > sticky > ROUTER > default member ──
    if squad is not None:
        via = "sticky"
        target = _sticky_only_target(state, group_state, ordered, pool_ids)
        if target is None:
            routed = await squad_runtime.route_member(
                db,
                squad=squad,
                text=inbound.user_text,
                pool=ordered,
                identity_id=identity_id,
                channel=channel,
            )
            if routed is not None and routed.id in pool_ids:
                target, via = routed, "router"
            else:
                target, via = current_agent, "default"
        if target is None:
            return _direct(base, _copy.not_open(lang=lang))
        # Only an *explicit* switch pins stickiness; the ROUTER / default
        # route is recomputed every turn so the team keeps routing freely.
        if via == "sticky":
            await _upsert_state(
                db,
                channel=channel,
                peer_key=peer_key,
                sender_key=write_sender_key,
                active_agent=target,
            )
        base.action = "run"
        base.target_agent_id = target.id
        base.target_workspace_id = target.workspace_id
        base.agent_name = target.name
        base.user_text = inbound.user_text
        base.switched = False
        await _audit(
            db,
            channel,
            "channel.route.run",
            identity_id,
            sender_key,
            extra={
                "agent_id": str(target.id),
                "switched": False,
                "squad_id": str(squad.id),
                "via": via,
            },
        )
        return base

    # ── Normal message: per-sender > group stickiness > default ──
    target = current_agent
    if target is None:
        return _direct(base, _copy.not_open(lang=lang))

    await _upsert_state(
        db, channel=channel, peer_key=peer_key, sender_key=write_sender_key, active_agent=target
    )
    base.action = "run"
    base.target_agent_id = target.id
    base.target_workspace_id = target.workspace_id
    base.agent_name = target.name
    base.user_text = inbound.user_text
    base.switched = False
    await _audit(
        db,
        channel,
        "channel.route.run",
        identity_id,
        sender_key,
        extra={"agent_id": str(target.id), "switched": False},
    )
    return base


async def _handle_command(
    db: AsyncSession,
    *,
    base: RouteOutcome,
    channel: Channel,
    routing: RoutingConfig,
    cmd: _commands.ParsedCommand,
    ordered: list[Agent],
    sender_key: str,
    current_agent: Agent | None,
    menu_options: list[dict],
    term: str,
    identity_id: uuid.UUID | None,
) -> RouteOutcome | None:
    lang = base.lang
    peer_key = base.peer_key
    options_copy = _copy_options(ordered)

    if cmd.code == _commands.CMD_HELP:
        await _upsert_state(
            db,
            channel=channel,
            peer_key=peer_key,
            sender_key=sender_key,
            menu_options=menu_options,
        )
        current_name = (
            current_agent.name if current_agent else (ordered[0].name if ordered else term)
        )
        base.menu_options = options_copy
        return _direct(
            base,
            _copy.welcome(
                lang=lang,
                term=term,
                current_name=current_name,
                options=options_copy,
                team=base.team_name,
            ),
        )

    if cmd.code == _commands.CMD_AGENTS_LIST:
        await _upsert_state(
            db,
            channel=channel,
            peer_key=peer_key,
            sender_key=sender_key,
            menu_options=menu_options,
        )
        base.menu_options = options_copy
        return _direct(
            base,
            _copy.agents_list(
                lang=lang,
                term=term,
                options=options_copy,
                total=len(ordered),
                team=base.team_name,
            ),
        )

    if cmd.code == _commands.CMD_WHOAMI:
        name = current_agent.name if current_agent else (ordered[0].name if ordered else term)
        return _direct(base, _copy.whoami(lang=lang, term=term, name=name, team=base.team_name))

    if cmd.code == _commands.CMD_RESET:
        await _upsert_state(
            db, channel=channel, peer_key=peer_key, sender_key=sender_key, clear_active=True
        )
        return _direct(base, _copy.reset_done(lang=lang))

    if cmd.code == _commands.CMD_WS_SWITCH:
        return _direct(
            base,
            await _handle_ws_switch(
                db, routing=routing, identity_id=identity_id, lang=lang, arg=cmd.arg
            ),
        )

    if cmd.code in (_commands.CMD_AGENTS_USE, _commands.CMD_MENTION):
        agent = _select_agent(cmd.arg, ordered)
        if agent is None:
            return _direct(base, _copy.not_found(lang=lang, term=term))
        await _upsert_state(
            db, channel=channel, peer_key=peer_key, sender_key=sender_key, active_agent=agent
        )
        # A mention carrying trailing text switches AND forwards the body
        # to the selected agent in the same turn.
        if cmd.code == _commands.CMD_MENTION and cmd.text:
            base.action = "run"
            base.target_agent_id = agent.id
            base.target_workspace_id = agent.workspace_id
            base.agent_name = agent.name
            base.user_text = cmd.text
            base.switched = True
            return None
        return _direct(base, _copy.switch_receipt(lang=lang, name=agent.name, team=base.team_name))

    # CMD_UNKNOWN and anything else.
    return _direct(base, _copy.unknown_command(lang=lang))


async def _handle_ws_switch(
    db: AsyncSession,
    *,
    routing: RoutingConfig,
    identity_id: uuid.UUID | None,
    lang: str,
    arg: str | None,
) -> str:
    """P0 ``/ws`` — list the identity's workspaces (user scope only)."""
    if routing.bind_scope != "user" or identity_id is None:
        return (
            "Workspace switching is only available on user-scoped channels."
            if lang == "en"
            else "工作区切换仅在 user 档渠道可用。"
        )
    ws_ids = await ws_svc.list_active_workspace_ids_for_identity(db, identity_id=identity_id)
    names: list[str] = []
    repo = WorkspaceRepository(db)
    for ws_id in ws_ids:
        ws = await repo.get(ws_id)
        if ws is not None:
            names.append(ws.name)
    listing = "\n".join(f"· {n}" for n in names) or "—"
    if lang == "en":
        return f"Your workspaces:\n{listing}\n\nAgents across them are already in /agents."
    return f"你的工作区:\n{listing}\n\n这些工作区的助手已合并在 /agents 中。"


# ─── Decision helpers ────────────────────────────────────────
def _sticky_target(
    state: ChannelConversationState | None,
    ordered: list[Agent],
    pool_ids: set[uuid.UUID],
    default_agent_id: uuid.UUID | None,
) -> Agent | None:
    if state is not None and state.active_agent_id in pool_ids:
        return next((a for a in ordered if a.id == state.active_agent_id), None)
    if default_agent_id is not None and default_agent_id in pool_ids:
        return next((a for a in ordered if a.id == default_agent_id), None)
    return ordered[0] if ordered else None


def _effective_sticky_target(
    state: ChannelConversationState | None,
    group_state: ChannelConversationState | None,
    ordered: list[Agent],
    pool_ids: set[uuid.UUID],
    default_agent_id: uuid.UUID | None,
) -> Agent | None:
    """Per-sender override > shared group route > default > first.

    ``state`` is the sender's own row (per-sender mode) or the shared row
    (P0 / DM). ``group_state`` is the shared row used as the fallback when
    a per-sender override exists but doesn't pin an in-pool agent.
    """
    if state is not None and state.active_agent_id in pool_ids:
        return next((a for a in ordered if a.id == state.active_agent_id), None)
    if (
        group_state is not None
        and group_state is not state
        and group_state.active_agent_id in pool_ids
    ):
        return next((a for a in ordered if a.id == group_state.active_agent_id), None)
    if default_agent_id is not None and default_agent_id in pool_ids:
        return next((a for a in ordered if a.id == default_agent_id), None)
    return ordered[0] if ordered else None


def _sticky_only_target(
    state: ChannelConversationState | None,
    group_state: ChannelConversationState | None,
    ordered: list[Agent],
    pool_ids: set[uuid.UUID],
) -> Agent | None:
    """Sticky route only — sender row then shared group row — no default.

    Used by squad scope so the ROUTER runs *between* stickiness and the
    default member (precedence: explicit > sticky > ROUTER > default).
    """
    if state is not None and state.active_agent_id in pool_ids:
        return next((a for a in ordered if a.id == state.active_agent_id), None)
    if (
        group_state is not None
        and group_state is not state
        and group_state.active_agent_id in pool_ids
    ):
        return next((a for a in ordered if a.id == group_state.active_agent_id), None)
    return None


def _select_agent(arg: str | None, ordered: list[Agent]) -> Agent | None:
    """Resolve ``/agent <alias|#>`` or ``@alias`` against the current pool."""
    if not arg:
        return None
    arg = arg.strip()
    if arg.isdigit():
        idx = int(arg)
        if 1 <= idx <= len(ordered):
            return ordered[idx - 1]
        return None
    lowered = arg.lower()
    for a in ordered:
        if (a.name or "").lower() == lowered:
            return a
    # prefix match as a convenience
    for a in ordered:
        if (a.name or "").lower().startswith(lowered):
            return a
    return None


def _build_menu_options(ordered: list[Agent]) -> list[dict]:
    return [
        {
            "alias": a.name,
            "agent_id": str(a.id),
            "workspace_id": str(a.workspace_id),
        }
        for a in ordered[:_MENU_TOP_N]
    ]


def _copy_options(ordered: list[Agent]) -> list[tuple[int, str, str | None]]:
    return [(i + 1, a.name, (a.description or None)) for i, a in enumerate(ordered[:_MENU_TOP_N])]


def _match_menu_number(
    state: ChannelConversationState | None, digit: str, pool_ids: set[uuid.UUID]
) -> uuid.UUID | None:
    if state is None:
        return None
    options = state.last_menu_options_json or []
    idx = int(digit)
    if not (1 <= idx <= len(options)):
        return None
    agent_id = _coerce_uuid((options[idx - 1] or {}).get("agent_id"))
    if agent_id is None or agent_id not in pool_ids:
        return None
    return agent_id


def _policy_block(policy: str, *, identity_id: uuid.UUID | None, channel: Channel, sender: str):
    """Return a ``_copy`` callable when the policy blocks, else ``None``."""
    if policy == "open":
        return None
    if policy == "disabled":
        return _copy.not_open
    if policy == "pairing":
        return None if identity_id is not None else _copy.not_bound
    if policy == "allowlist":
        rules = channel.sender_allowlist_json or {}
        allow = {str(s).strip() for s in (rules.get("allow") or []) if str(s).strip()}
        if identity_id is not None or sender in allow:
            return None
        return _copy.not_open
    return None


# ─── Misc helpers ────────────────────────────────────────────
def _direct(base: RouteOutcome, text: str) -> RouteOutcome:
    base.action = "direct"
    base.reply_text = text
    return base


async def _resolve_lang(
    db: AsyncSession, *, channel: Channel, identity_id: uuid.UUID | None
) -> str:
    identity_locale: str | None = None
    if identity_id is not None:
        identity = await IdentityRepository(db).get(identity_id)
        if identity is not None and isinstance(identity.profile_json, dict):
            identity_locale = identity.profile_json.get("locale")
    workspace_lang: str | None = None
    ws = await WorkspaceRepository(db).get(channel.workspace_id)
    if ws is not None and isinstance(ws.branding_json, dict):
        workspace_lang = ws.branding_json.get("language") or ws.branding_json.get("locale")
    return _copy.pick_lang(identity_locale=identity_locale, workspace_lang=workspace_lang)


async def _resolve_agent_term(db: AsyncSession, *, workspace_id: uuid.UUID) -> str:
    ws = await WorkspaceRepository(db).get(workspace_id)
    if ws is not None and isinstance(ws.branding_json, dict):
        term = ws.branding_json.get("agent_term")
        if term:
            return str(term)
    return "agent"


async def _audit(
    db: AsyncSession,
    channel: Channel,
    action: str,
    identity_id: uuid.UUID | None,
    sender_key: str,
    *,
    extra: dict | None = None,
) -> None:
    metadata = {
        "channel_id": str(channel.id),
        "external_user_hash": _redact(sender_key),
    }
    if extra:
        metadata.update(extra)
    await audit_svc.record(
        db,
        action=action,
        actor_identity_id=identity_id,
        workspace_id=channel.workspace_id,
        resource_type="channel",
        resource_id=channel.id,
        summary=action,
        metadata=metadata,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _redact(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "BindingOverride",
    "RouteOutcome",
    "RoutingConfig",
    "link_identity",
    "mint_bind_code",
    "normalize_routing_config",
    "parse_routing_config",
    "resolve_binding",
    "resolve_identity",
    "resolve_pool",
    "resolve_route",
]
