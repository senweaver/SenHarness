"""Squad ``ROUTER`` runtime (P2).

A squad bound to a channel (``bind_scope=squad``) exposes its **member
agents** as the candidate pool. This module owns the two squad-specific
pieces the channel router leans on:

1. **Pool resolution** — :func:`resolve_squad_pool` turns a squad into its
   accessible member agents, enforcing the same workspace + visibility
   boundary as the workspace / user scopes. A member that lives outside the
   squad's workspace (which equals the channel's workspace) or that the
   caller cannot see is dropped, so a routed target can never escalate a
   sender into an agent they couldn't otherwise reach.

2. **Member selection (the ROUTER)** — :func:`route_member` picks which
   member should answer an inbound message. Two deterministic-testable
   strategies, tried in order:

   * a pure **rule** router (keyword → member ``agent_id``), unit-testable
     with no DB / no model;
   * an optional **LLM** router that asks a designated member to classify
     the message. The model turn rides the ``AgentBackend`` protocol via
     :func:`app.services.agent_runner.run_agent_one_shot` (never
     ``pydantic_ai.Agent`` directly), so it stays mockable in tests.

The ROUTER strategy is stored on ``squads.config_json.router`` — the
``Squad`` model already carries ``strategy=ROUTER`` + ``config_json``, so no
new column / migration is required. A squad with no router config falls back
to the default member (``default_member_agent_id`` → channel default →
highest-weight member), which keeps existing squads unaffected.

The channel router applies the full precedence
(explicit command / @alias / number > sticky > ROUTER > default member);
this module only answers "which member, given a free-text message".
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.agent import Agent, AgentVisibility
from app.db.models.channel import Channel
from app.db.models.squad import Squad
from app.repositories.agent import AgentRepository
from app.repositories.squad import SquadMemberRepository, SquadRepository

log = logging.getLogger(__name__)

_VALID_ROUTER_MODES = frozenset({"rule", "llm"})
_DIGITS = re.compile(r"\d+")


# ─── Router config ───────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class RouterRule:
    """A keyword → member rule. Pure-substring match (case-insensitive)."""

    keywords: tuple[str, ...]
    agent_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class SquadRouterConfig:
    """Typed view of ``squads.config_json.router`` (P2).

    * ``mode`` — ``rule`` (default) or ``llm``. The rule router always runs
      first regardless of mode; ``llm`` only adds the model fallback when no
      rule hits.
    * ``default_member_agent_id`` — the member used when nothing else
      decides (the squad's "main" member).
    * ``router_agent_id`` — the member that classifies in ``llm`` mode
      (defaults to the default member / first member).
    * ``rules`` — ordered keyword rules; the first hit (whose target is in
      the pool) wins.
    """

    mode: str = "rule"
    default_member_agent_id: uuid.UUID | None = None
    router_agent_id: uuid.UUID | None = None
    rules: tuple[RouterRule, ...] = ()


def _coerce_uuid(value: object) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return uuid.UUID(value.strip())
        except ValueError:
            return None
    return None


def parse_router_config(squad_config: dict | None) -> SquadRouterConfig:
    """Parse ``squads.config_json`` into a typed :class:`SquadRouterConfig`.

    Defensive: malformed entries are dropped (never raises) so a hand-edited
    squad row can't crash the dispatcher — exactly like the channel routing
    config parser.
    """
    raw = (squad_config or {}).get("router")
    if not isinstance(raw, dict):
        raw = {}

    mode = str(raw.get("mode") or "rule").strip().lower()
    if mode not in _VALID_ROUTER_MODES:
        mode = "rule"

    rules: list[RouterRule] = []
    rules_raw = raw.get("rules")
    if isinstance(rules_raw, (list, tuple)):
        for item in rules_raw:
            if not isinstance(item, dict):
                continue
            kw_raw = item.get("keywords")
            if not isinstance(kw_raw, (list, tuple)):
                continue
            keywords = tuple(kw for kw in (str(k).strip().lower() for k in kw_raw) if kw)
            agent_id = _coerce_uuid(item.get("agent_id"))
            if not keywords or agent_id is None:
                continue
            rules.append(RouterRule(keywords=keywords, agent_id=agent_id))

    return SquadRouterConfig(
        mode=mode,
        default_member_agent_id=_coerce_uuid(raw.get("default_member_agent_id")),
        router_agent_id=_coerce_uuid(raw.get("router_agent_id")),
        rules=tuple(rules),
    )


def normalize_router_config(squad_config: dict | None) -> dict:
    """Canonicalize a squad ``config_json`` blob, normalizing ``router``.

    Preserves any non-router keys the squad already carries, and produces a
    tidy ``router`` sub-object (canonical casing, stringified ids, dropped
    stray keys). ``router`` is omitted entirely when empty so plain squads
    stay ``{}``.
    """
    base = dict(squad_config or {})
    cfg = parse_router_config(base)
    router: dict = {"mode": cfg.mode}
    if cfg.default_member_agent_id is not None:
        router["default_member_agent_id"] = str(cfg.default_member_agent_id)
    if cfg.router_agent_id is not None:
        router["router_agent_id"] = str(cfg.router_agent_id)
    if cfg.rules:
        router["rules"] = [
            {"keywords": list(r.keywords), "agent_id": str(r.agent_id)} for r in cfg.rules
        ]
    # Only persist a router block when it carries more than the default mode.
    if router == {"mode": "rule"}:
        base.pop("router", None)
    else:
        base["router"] = router
    return base


# ─── Pool resolution ─────────────────────────────────────────
def _agent_accessible(agent: Agent, identity_id: uuid.UUID | None) -> bool:
    """Mirror of ``channel_routing._agent_accessible`` (kept local to avoid a
    routing ↔ squad-runtime import cycle).
    """
    visibility = str(getattr(agent, "visibility", AgentVisibility.WORKSPACE))
    if visibility in (AgentVisibility.WORKSPACE.value, AgentVisibility.PUBLIC.value):
        return True
    if visibility == AgentVisibility.PRIVATE.value:
        return identity_id is not None and agent.created_by == identity_id
    return True


async def resolve_squad(
    db: AsyncSession, *, squad_id: uuid.UUID | None, workspace_id: uuid.UUID
) -> Squad | None:
    """Load a squad iff it exists and belongs to ``workspace_id``.

    Cross-workspace squad references resolve to ``None`` (no escalation):
    a channel can only route through a squad in its own workspace.
    """
    if squad_id is None:
        return None
    squad = await SquadRepository(db).get(squad_id)
    if squad is None or squad.deleted_at is not None:
        return None
    if squad.workspace_id != workspace_id:
        return None
    return squad


async def resolve_squad_pool(
    db: AsyncSession, *, squad: Squad, identity_id: uuid.UUID | None
) -> list[Agent]:
    """The squad's accessible member agents, in member-weight order.

    Filters to members that (a) still exist, (b) live in the squad's own
    workspace, and (c) are visible to the caller — the same boundary the
    workspace / user scopes enforce. Members are returned highest-weight
    first (the repository's ordering) so the squad's intended priority drives
    the numbered menu + default-member fallback.
    """
    members = await SquadMemberRepository(db).list_for_squad(squad.id)
    repo = AgentRepository(db)
    pool: list[Agent] = []
    seen: set[uuid.UUID] = set()
    for m in members:
        if m.agent_id in seen:
            continue
        agent = await repo.get(m.agent_id)
        if agent is None or agent.deleted_at is not None:
            continue
        if agent.workspace_id != squad.workspace_id:
            continue
        if not _agent_accessible(agent, identity_id):
            continue
        seen.add(agent.id)
        pool.append(agent)
    return pool


def default_member(
    pool: list[Agent],
    cfg: SquadRouterConfig,
    *,
    channel_default_agent_id: uuid.UUID | None = None,
) -> Agent | None:
    """Resolve the squad's default member: configured > channel default > first.

    Always returns a member that is actually in ``pool`` (or ``None`` for an
    empty squad) so the channel router never falls back outside the squad.
    """
    if not pool:
        return None
    by_id = {a.id: a for a in pool}
    if cfg.default_member_agent_id in by_id:
        return by_id[cfg.default_member_agent_id]
    if channel_default_agent_id in by_id:
        return by_id[channel_default_agent_id]
    return pool[0]


# ─── ROUTER member selection ─────────────────────────────────
def select_member_by_rules(
    text: str | None, cfg: SquadRouterConfig, pool: list[Agent]
) -> Agent | None:
    """Pure keyword router — first rule whose keyword hits + target in pool."""
    if not text or not cfg.rules:
        return None
    lowered = text.strip().lower()
    if not lowered:
        return None
    by_id = {a.id: a for a in pool}
    for rule in cfg.rules:
        if any(kw in lowered for kw in rule.keywords):
            agent = by_id.get(rule.agent_id)
            if agent is not None:
                return agent
    return None


def parse_router_choice(reply: str | None, pool: list[Agent]) -> Agent | None:
    """Map an LLM router reply to a member: a number indexes the pool, else
    a (case-insensitive) name / prefix match.
    """
    if not reply:
        return None
    text = reply.strip()
    m = _DIGITS.search(text)
    if m is not None:
        idx = int(m.group())
        if 1 <= idx <= len(pool):
            return pool[idx - 1]
    lowered = text.lower()
    for a in pool:
        if (a.name or "").lower() == lowered:
            return a
    for a in pool:
        name = (a.name or "").lower()
        if name and name in lowered:
            return a
    return None


def build_router_prompt(squad: Squad, pool: list[Agent], text: str) -> str:
    """Classification prompt for the LLM router — enumerate members, ask for
    the best one by number. English (a log/ops-facing internal prompt; the
    user never sees it).
    """
    lines = []
    for i, a in enumerate(pool, start=1):
        desc = f" — {a.description}" if a.description else ""
        lines.append(f"{i}. {a.name}{desc}")
    listing = "\n".join(lines)
    return (
        f"You are the router for the team \u201c{squad.name}\u201d. "
        f"Pick the single best member to handle the user's message.\n\n"
        f"Members:\n{listing}\n\n"
        f"User message:\n{text}\n\n"
        f"Reply with ONLY the member's number."
    )


async def route_member(
    db: AsyncSession,
    *,
    squad: Squad,
    text: str | None,
    pool: list[Agent],
    identity_id: uuid.UUID | None,
    channel: Channel,
) -> Agent | None:
    """Pick the member that should handle ``text`` — rule first, then LLM.

    Returns ``None`` when no strategy decides; the channel router then falls
    back to the squad default member. Never returns a member outside ``pool``.
    """
    if not pool:
        return None
    cfg = parse_router_config(squad.config_json)

    picked = select_member_by_rules(text, cfg, pool)
    if picked is not None:
        return picked

    if cfg.mode != "llm" or not text:
        return None
    return await _select_member_by_llm(
        db, squad=squad, cfg=cfg, text=text, pool=pool, identity_id=identity_id, channel=channel
    )


async def _select_member_by_llm(
    db: AsyncSession,
    *,
    squad: Squad,
    cfg: SquadRouterConfig,
    text: str,
    pool: list[Agent],
    identity_id: uuid.UUID | None,
    channel: Channel,
) -> Agent | None:
    """Ask the router member to classify the message (via ``AgentBackend``).

    The model turn runs through ``run_agent_one_shot`` so it is fully
    mockable and never touches ``pydantic_ai.Agent`` directly. A dedicated
    router session keeps the classification turns out of the members' own
    conversation memory.
    """
    # Lazy import keeps ``squad_runtime`` import-light and dodges any
    # routing ↔ runner import cycle.
    from app.services import agent_runner as runner

    pool_ids = {a.id for a in pool}
    router_agent_id = cfg.router_agent_id if cfg.router_agent_id in pool_ids else None
    if router_agent_id is None:
        dm = default_member(pool, cfg, channel_default_agent_id=channel.default_agent_id)
        router_agent_id = dm.id if dm is not None else None
    if router_agent_id is None:
        return None

    session_obj = await runner.ensure_channel_session(
        db,
        workspace_id=squad.workspace_id,
        channel_id=channel.id,
        thread_key=f"squad-router:{squad.id}",
        subject_id=router_agent_id,
        title_hint=f"[router] {squad.name}",
    )
    prompt = build_router_prompt(squad, pool, text)
    try:
        result = await runner.run_agent_one_shot(
            db,
            workspace_id=squad.workspace_id,
            agent_id=router_agent_id,
            session_id=session_obj.id,
            identity_id=identity_id,
            user_text=prompt,
        )
    except Exception:  # pragma: no cover — router must never break dispatch
        log.warning("squad ROUTER llm selection failed for squad=%s", squad.id, exc_info=True)
        return None
    if result.error:
        return None
    return parse_router_choice(result.final_text, pool)


__all__ = [
    "RouterRule",
    "SquadRouterConfig",
    "build_router_prompt",
    "default_member",
    "normalize_router_config",
    "parse_router_choice",
    "parse_router_config",
    "resolve_squad",
    "resolve_squad_pool",
    "route_member",
    "select_member_by_rules",
]
