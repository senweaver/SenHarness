"""Natural-language handoff / proactive routing (P1).

The default-entry **main agent** (the channel's ``default_agent_id``) is the
zero-friction landing spot: a first message just talks to it. From there the
conversation can move to a specialist either by the user's explicit command
(``/agent``, ``@alias``, a menu number — all P0) or, when the inbound is a
plain message, by a **deterministic keyword/intent router** configured on the
channel. This keeps the LLM-driven "shall I transfer you to 报销助手?" idea
expressible without the routing layer itself calling a model — the actual
agent turns still run through the ``AgentBackend`` path
(``run_agent_one_shot``), so everything stays mockable in tests.

A rule is ``{keywords: [...], target: "<alias|#index|agent_id>", mode}``:

* ``mode="switch"`` (default) — auto-switch the active route to the target
  and forward the message to it (announced via the presenter's attribution /
  footer), i.e. a deterministic handoff.
* ``mode="suggest"`` — don't switch; reply with a proactive proposal that the
  user can accept by replying the number / ``@alias`` (the main agent stays in
  control of the actual turn).

Matching is a case-insensitive substring test, evaluated in rule order; the
first rule with a hit wins. Empty config ⇒ no handoff ⇒ pure P0 behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

HANDOFF_MODES = ("switch", "suggest")


@dataclass(frozen=True, slots=True)
class HandoffRule:
    keywords: tuple[str, ...]
    target: str
    mode: str = "switch"


def parse_handoff_rules(raw: object) -> tuple[HandoffRule, ...]:
    """Parse the stored ``handoff_rules`` blob into typed rules.

    Defensive: malformed entries are dropped rather than raising, so a
    hand-edited config can never crash the dispatcher.
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    rules: list[HandoffRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kw_raw = item.get("keywords")
        if not isinstance(kw_raw, (list, tuple)):
            continue
        keywords = tuple(kw for kw in (str(k).strip().lower() for k in kw_raw) if kw)
        target = str(item.get("target") or "").strip()
        if not keywords or not target:
            continue
        mode = str(item.get("mode") or "switch").strip().lower()
        if mode not in HANDOFF_MODES:
            mode = "switch"
        rules.append(HandoffRule(keywords=keywords, target=target, mode=mode))
    return tuple(rules)


def dump_handoff_rules(rules: tuple[HandoffRule, ...]) -> list[dict]:
    return [{"keywords": list(r.keywords), "target": r.target, "mode": r.mode} for r in rules]


def match_handoff(text: str | None, rules: tuple[HandoffRule, ...]) -> HandoffRule | None:
    """Return the first rule whose any keyword occurs in ``text``."""
    if not text or not rules:
        return None
    lowered = text.strip().lower()
    if not lowered:
        return None
    for rule in rules:
        if any(kw in lowered for kw in rule.keywords):
            return rule
    return None


__all__ = [
    "HANDOFF_MODES",
    "HandoffRule",
    "dump_handoff_rules",
    "match_handoff",
    "parse_handoff_rules",
]
