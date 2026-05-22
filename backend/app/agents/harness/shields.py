"""Shields harness — wraps ``pydantic-ai-shields`` capabilities.

Activated per policy:

  ``policy["approvals"]``
      ``true``                 → ToolGuard with DEFAULT_APPROVAL_TOOLS.
      ``list[str]``            → ToolGuard with that exact list.
      ``{"tools": [...], "blocked": [...]}`` → fine-grained.

  ``policy["shields"]``
      ``{"pii": true, "injection": "high", "secrets": true, ...}``

  ``policy["budget"]``
      ``{"usd": 0.5, "on_exceed": "warn" | "stop"}``

Every sub-module returns ``None`` when its dependency isn't installed, keeping
SenHarness degradable.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from app.agents.harness.approvals import (
    DEFAULT_APPROVAL_TOOLS,
    resolve_require_approval,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Tool approval gate
# ──────────────────────────────────────────────────────────────────
def build_tool_guard(
    *,
    policy: dict[str, Any] | None,
    workspace_id: uuid.UUID,
    session_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    run_id: uuid.UUID | None,
    requested_by_identity_id: uuid.UUID | None,
) -> Any | None:
    require_approval = resolve_require_approval(policy)
    blocked = _resolve_blocked(policy)
    if not require_approval and not blocked:
        return None

    try:
        from pydantic_ai_shields import ToolGuard
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-shields not installed; ToolGuard disabled")
        return None

    # Lazy import to avoid pulling services during harness tests.
    from app.services.approval import make_approval_callback

    callback = (
        make_approval_callback(
            workspace_id=workspace_id,
            session_id=session_id,
            agent_id=agent_id,
            run_id=run_id,
            requested_by_identity_id=requested_by_identity_id,
            ttl_seconds=int((policy or {}).get("approval_ttl_seconds", 300)),
        )
        if require_approval
        else None
    )

    try:
        return ToolGuard(
            blocked=list(blocked),
            require_approval=list(require_approval),
            approval_callback=callback,
        )
    except Exception as e:  # pragma: no cover
        log.warning("ToolGuard init failed: %s", e)
        return None


def _resolve_blocked(policy: dict[str, Any] | None) -> list[str]:
    if not policy:
        return []
    p = policy.get("blocked_tools") or policy.get("approvals_blocked")
    if isinstance(p, list):
        return [str(x) for x in p]
    return []


# ──────────────────────────────────────────────────────────────────
# Input / Output content guards (PII, prompt injection, secrets)
# ──────────────────────────────────────────────────────────────────
# M0.8: distinguish "shields key absent" (→ apply defaults) from
# "shields explicitly set to []" (→ author opted out). The first
# branch only fires for new agents whose ``policy.shields`` was never
# touched, so existing rows keep behaving exactly like before.
_DEFAULT_SHIELDS_CONFIG = {"pii": "log", "secrets": True, "injection": "medium"}


def build_content_guards(policy: dict[str, Any] | None) -> list[Any]:
    """Return a list of shield capabilities for content guarding.

    Default-ON posture: when ``policy`` is ``None`` or has no
    ``shields`` key, every new agent gets baseline PII / secret /
    prompt-injection guards. Setting ``shields`` to ``False`` or
    ``[]`` is treated as an explicit opt-out so authors can still
    disable the layer when needed.
    """
    cfg: Any
    if policy is None or "shields" not in policy:
        cfg = dict(_DEFAULT_SHIELDS_CONFIG)
    else:
        cfg = policy["shields"]
        if cfg is False or cfg is None:
            return []
        if cfg == []:
            return []
        if cfg is True:
            cfg = dict(_DEFAULT_SHIELDS_CONFIG)
        if not isinstance(cfg, dict):
            return []

    try:
        from pydantic_ai_shields import (
            PiiDetector,
            PromptInjection,
            SecretRedaction,
        )
    except ImportError:  # pragma: no cover
        log.info("pydantic-ai-shields not installed; content guards disabled")
        return []

    out: list[Any] = []

    pii = cfg.get("pii")
    if pii:
        action = "block" if pii is True or pii == "block" else "log"
        try:
            out.append(PiiDetector(action=action))
        except Exception as e:  # pragma: no cover
            log.warning("PiiDetector init failed: %s", e)

    injection = cfg.get("injection")
    if injection:
        sensitivity = injection if injection in ("low", "medium", "high") else "medium"
        try:
            out.append(PromptInjection(sensitivity=sensitivity))
        except Exception as e:  # pragma: no cover
            log.warning("PromptInjection init failed: %s", e)

    if cfg.get("secrets"):
        try:
            out.append(SecretRedaction())
        except Exception as e:  # pragma: no cover
            log.warning("SecretRedaction init failed: %s", e)

    keywords = cfg.get("blocked_keywords")
    if keywords:
        try:
            from pydantic_ai_shields import BlockedKeywords

            out.append(BlockedKeywords(keywords=list(keywords)))
        except Exception as e:  # pragma: no cover
            log.warning("BlockedKeywords init failed: %s", e)

    return out


# ──────────────────────────────────────────────────────────────────
# Cost + budget guard
# ──────────────────────────────────────────────────────────────────
def build_cost_tracking(policy: dict[str, Any] | None, *, primary_model: Any) -> Any | None:
    if not policy:
        return None
    budget = policy.get("budget")
    if not budget:
        return None

    budget_usd = None
    if isinstance(budget, (int, float)):
        budget_usd = float(budget)
    elif isinstance(budget, dict):
        val = budget.get("usd") or budget.get("limit")
        if val is not None:
            budget_usd = float(val)

    try:
        from pydantic_ai_shields import CostTracking
    except ImportError:  # pragma: no cover
        return None

    try:
        model_name = getattr(primary_model, "model_name", None) or str(primary_model)
    except Exception:  # pragma: no cover
        model_name = None

    try:
        return CostTracking(model_name=model_name, budget_usd=budget_usd)
    except Exception as e:  # pragma: no cover
        log.warning("CostTracking init failed: %s", e)
        return None


__all__ = [
    "DEFAULT_APPROVAL_TOOLS",
    "build_content_guards",
    "build_cost_tracking",
    "build_tool_guard",
]
