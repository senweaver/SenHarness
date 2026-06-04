"""Per-model behaviour profile — drives reasoning toggles and mode routing.

Replaces brand-name pattern matching (``_qwen3_no_think`` / ``_deepseek_no_think``
/ ``_flash_non_reasoning_override``) with a single profile lookup. Sources, in
priority order:

  1. ``provider_models.metadata_json["profile"]`` — per-workspace DB override.
  2. ``BUILTIN_PROFILES`` — catalog-default keyed by ``(provider_kind, pattern)``.
  3. Generic safe default (no reasoning toggle, no flash routing).

The runner reads the resolved profile and applies it verbatim, so adding
support for a new hybrid-thinking model is one entry in ``BUILTIN_PROFILES``
(or one JSONB patch on ``provider_models``) — no runner edits, no migration.

Profile JSON shape (matches DB override on ``metadata_json["profile"]``)::

    {
      "reasoning": {
        "supported": true,        // model exposes a thinking phase at all
        "hybrid": true,           // single endpoint, toggle via wire param
        "default": "off",         // "on" | "off" — phase when caller is silent
        "tool_call_safe": false,  // safe to keep thinking on across multi-turn
                                  // tool calls. Hybrid models that require
                                  // ``reasoning_content`` round-trip in the
                                  // assistant tool-call message set this to
                                  // ``false`` so the harness defaults to off.
        "enable":  { ...payload... },   // see PAYLOAD KEYS below
        "disable": { ...payload... }
      },
      "flash_alternative": "deepseek-chat"  // optional: when mode=flash, route
                                            // to this model name (same provider).
    }

PAYLOAD KEYS (``enable`` / ``disable``)::

    "model_settings": { "reasoning_effort": "high", "thinking": false, ... }
        merged onto ``agent.model_settings``.
    "extra_body":     { "thinking": {"type": "disabled"}, ... }
        merged into ``agent.model_settings["extra_body"]``.
    "system_suffix":  "/no_think"
        appended to the assembled system prompt (Qwen3 honours this).
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ReasoningProfile:
    supported: bool = False
    hybrid: bool = False
    default: str = "off"
    tool_call_safe: bool = True
    # Whether the upstream accepts a ``reasoning_effort`` strength knob.
    # Defaults ``False`` because several reasoning models (qwen3, kimi,
    # deepseek-reasoner) reject the parameter — the runner only emits
    # ``reasoning_effort`` when this is ``True``.
    supports_effort: bool = False
    enable: dict[str, Any] = field(default_factory=dict)
    disable: dict[str, Any] = field(default_factory=dict)
    # Operator-set effort knob from the per-row dialog. Distinct from
    # ``enable.model_settings.reasoning_effort`` (which is part of the
    # builtin wire payload) so a workspace can pick a global effort
    # without overwriting the SDK-specific enable dict. The runner
    # applies this *after* the builtin enable payload and *before* the
    # per-run policy override. ``None`` means "no preference — keep
    # whatever the builtin enable payload already sets".
    preferred_effort: str | None = None


@dataclass(slots=True)
class ModelProfile:
    reasoning: ReasoningProfile = field(default_factory=ReasoningProfile)
    flash_alternative: str | None = None


# ─── Builtin defaults ─────────────────────────────────────────────
# Keyed by ``(provider_kind, fnmatch-pattern)``. First match (top-down) wins.
# Patterns are case-insensitive; model names are lowercased before comparison.

_HYBRID_KIMI = ModelProfile(
    reasoning=ReasoningProfile(
        supported=True,
        hybrid=True,
        default="off",
        tool_call_safe=False,
        # Kimi's OpenAI-compatible endpoint treats the presence of
        # ``reasoning_effort`` as a thinking-enabled signal regardless
        # of ``thinking.type``. The disable payload must therefore
        # avoid setting ``reasoning_effort`` at all and rely solely on
        # ``thinking.type=disabled`` to keep the wire request consistent
        # with what the server enforces.
        enable={
            "extra_body": {"thinking": {"type": "enabled"}},
        },
        disable={
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    ),
)

_HYBRID_QWEN3 = ModelProfile(
    reasoning=ReasoningProfile(
        supported=True,
        hybrid=True,
        default="off",
        tool_call_safe=False,
        enable={"extra_body": {"enable_thinking": True}},
        disable={
            "extra_body": {"enable_thinking": False},
            "system_suffix": "/no_think",
        },
    ),
)

_HYBRID_GLM5 = ModelProfile(
    reasoning=ReasoningProfile(
        supported=True,
        hybrid=True,
        default="off",
        tool_call_safe=False,
        supports_effort=True,
        enable={"model_settings": {"reasoning_effort": "high"}},
        disable={"model_settings": {"thinking": False}},
    ),
)

_DEEPSEEK_V4 = ModelProfile(
    reasoning=ReasoningProfile(
        supported=True,
        hybrid=True,
        default="off",
        tool_call_safe=False,
        enable={"extra_body": {"thinking": {"type": "enabled"}}},
        disable={"extra_body": {"thinking": {"type": "disabled"}}},
    ),
)

# Pure reasoners that reject ``reasoning_effort`` (kimi-k2-thinking,
# deepseek-reasoner): thinking is always on, no strength knob.
_PURE_REASONER = ModelProfile(
    reasoning=ReasoningProfile(supported=True, hybrid=False, default="on"),
)

# OpenAI o-series reasoners accept ``reasoning_effort``.
_PURE_REASONER_EFFORT = ModelProfile(
    reasoning=ReasoningProfile(supported=True, hybrid=False, default="on", supports_effort=True),
)

BUILTIN_PROFILES: list[tuple[str, str, ModelProfile]] = [
    # ─── DeepSeek ─────────────────────────────────────────
    ("deepseek", "deepseek-v4-*", _DEEPSEEK_V4),
    (
        "deepseek",
        "deepseek-reasoner",
        ModelProfile(
            reasoning=_PURE_REASONER.reasoning,
            flash_alternative="deepseek-chat",
        ),
    ),
    # ─── Moonshot Kimi ────────────────────────────────────
    ("moonshot", "kimi-k2.6*", _HYBRID_KIMI),
    ("moonshot", "kimi-k2-thinking*", _PURE_REASONER),
    ("kimi_code", "kimi-k2.6*", _HYBRID_KIMI),
    ("kimi_code", "kimi-k2-thinking*", _PURE_REASONER),
    # ─── DashScope / Bailian Qwen3 ────────────────────────
    ("dashscope", "qwen3*", _HYBRID_QWEN3),
    ("bailian_token", "qwen3*", _HYBRID_QWEN3),
    ("bailian_coding", "qwen3*", _HYBRID_QWEN3),
    # ─── Zhipu GLM-5 family ───────────────────────────────
    ("zhipu", "glm-5*", _HYBRID_GLM5),
    # ─── OpenAI / Anthropic reasoners (dedicated SKUs) ────
    ("openai", "o3*", _PURE_REASONER_EFFORT),
    ("openai", "o4-*", _PURE_REASONER_EFFORT),
]


def _match_builtin(provider_kind: str, model_name: str) -> ModelProfile | None:
    """Return the first matching builtin profile, or ``None``."""
    name = (model_name or "").lower()
    for prov, pattern, profile in BUILTIN_PROFILES:
        if prov != provider_kind:
            continue
        if fnmatch.fnmatchcase(name, pattern.lower()):
            return profile
    return None


def _merge_reasoning(base: ReasoningProfile, override: Any) -> ReasoningProfile:
    if not isinstance(override, dict):
        return base
    effort_override = override.get("preferred_effort", base.preferred_effort)
    cleaned_effort: str | None
    if effort_override in (None, ""):
        cleaned_effort = None
    else:
        cleaned_effort = str(effort_override).strip().lower() or None
    return ReasoningProfile(
        supported=bool(override.get("supported", base.supported)),
        hybrid=bool(override.get("hybrid", base.hybrid)),
        default=str(override.get("default", base.default)),
        tool_call_safe=bool(override.get("tool_call_safe", base.tool_call_safe)),
        supports_effort=bool(override.get("supports_effort", base.supports_effort)),
        enable=dict(override.get("enable") or base.enable),
        disable=dict(override.get("disable") or base.disable),
        preferred_effort=cleaned_effort,
    )


def _merge_profile(base: ModelProfile, override: Any) -> ModelProfile:
    if not isinstance(override, dict):
        return base
    return ModelProfile(
        reasoning=_merge_reasoning(base.reasoning, override.get("reasoning")),
        flash_alternative=override.get("flash_alternative", base.flash_alternative),
    )


def resolve_profile(
    *,
    provider_kind: str,
    model_name: str,
    db_metadata: dict[str, Any] | None = None,
) -> ModelProfile:
    """Resolve the effective profile for ``(provider_kind, model_name)``."""
    base = _match_builtin(provider_kind, model_name) or ModelProfile()
    if isinstance(db_metadata, dict):
        return _merge_profile(base, db_metadata.get("profile"))
    return base


def desired_thinking_state(*, profile: ModelProfile, policy: dict[str, Any] | None) -> str:
    """Return ``"on"`` or ``"off"`` — the wire-level thinking phase to request.

    Mode rules:
      * ``mode=thinking`` → ``"on"`` whenever the model exposes a reasoning
        phase. This is an explicit user opt-in, so ``tool_call_safe`` does not
        veto it; that flag only governs the silent-default path below.
      * ``mode=flash`` → always ``"off"`` (the flash branch may also have routed
        to ``flash_alternative`` earlier in the runner).
      * Otherwise → fall back to the profile's ``default``.
    """
    mode = str((policy or {}).get("mode") or "").strip().lower()
    reasoning = profile.reasoning
    if mode == "thinking":
        return "on" if reasoning.supported else "off"
    if mode == "flash":
        return "off"
    return reasoning.default if reasoning.default in ("on", "off") else "off"


def apply_reasoning_payload(
    *, model_settings: dict[str, Any], payload: dict[str, Any]
) -> str | None:
    """Merge a profile ``enable`` / ``disable`` payload onto model settings.

    Returns the optional ``system_suffix`` (e.g. ``"/no_think"``) the caller
    should append to the system prompt, or ``None`` when the payload doesn't
    carry one. Idempotent: calling twice with the same payload is a no-op.
    """
    ms_override = payload.get("model_settings")
    if isinstance(ms_override, dict):
        for k, v in ms_override.items():
            model_settings[k] = v
    eb_override = payload.get("extra_body")
    if isinstance(eb_override, dict):
        existing = model_settings.get("extra_body") or {}
        model_settings["extra_body"] = {**existing, **eb_override}
    suffix = payload.get("system_suffix")
    return suffix if isinstance(suffix, str) and suffix else None


def apply_reasoning_settings(
    *,
    profile: ModelProfile,
    model_settings: dict[str, Any],
    reasoning_payload: dict[str, Any],
    thinking_state: str,
    run_effort: str | None,
) -> None:
    """Fold a resolved reasoning profile into ``model_settings`` in place.

    This is the runtime-enforcement counterpart to ``resolve_profile``:

      * ``supported=false`` models pass an empty ``reasoning_payload`` and
        never receive a ``reasoning_effort`` knob.
      * ``reasoning_effort`` (operator-preferred or per-run ``run_effort``)
        is applied only when the model both supports a thinking phase and
        accepts the strength knob (``supports_effort``); otherwise any
        effort the payload introduced is stripped.
      * Tool-call-unsafe hybrids drop ``reasoning_effort`` whenever
        thinking is off so the wire request stays valid across tool calls.

    ``reasoning_payload`` is the already-selected ``enable`` / ``disable``
    dict (empty when the model has no thinking phase); ``run_effort`` is
    the per-run effort the caller resolved from policy + user intent.
    """
    reasoning = profile.reasoning
    if reasoning_payload:
        apply_reasoning_payload(model_settings=model_settings, payload=reasoning_payload)
    if reasoning.supported and reasoning.supports_effort:
        if reasoning.preferred_effort:
            model_settings["reasoning_effort"] = reasoning.preferred_effort
        if run_effort is not None:
            model_settings["reasoning_effort"] = run_effort
    else:
        model_settings.pop("reasoning_effort", None)
    if thinking_state == "off" and reasoning.hybrid and not reasoning.tool_call_safe:
        model_settings.pop("reasoning_effort", None)


async def load_provider_model_metadata(
    *, workspace_id: Any, provider_kind: str, model_name: str
) -> dict[str, Any] | None:
    """Load ``provider_models.metadata_json`` for the workspace's matching row.

    Returns ``None`` when no enabled provider/model row matches — callers fall
    back to the builtin profile defaults. The lookup is one SELECT join keyed
    on ``(workspace_id, kind, model)`` and uses a fresh AsyncSession so it
    composes cleanly inside the runner's hot path.
    """
    try:
        from sqlalchemy import select

        from app.db.models.model_provider import ModelProvider, ProviderModel
        from app.db.session import get_session_factory
    except ImportError:  # pragma: no cover
        return None

    factory = get_session_factory()
    try:
        async with factory() as session:
            stmt = (
                select(ProviderModel.metadata_json)
                .join(ModelProvider, ProviderModel.provider_id == ModelProvider.id)
                .where(
                    ModelProvider.workspace_id == workspace_id,
                    ModelProvider.deleted_at.is_(None),
                    ModelProvider.kind == provider_kind,
                    ProviderModel.model == model_name,
                )
                .limit(1)
            )
            row = (await session.execute(stmt)).first()
    except Exception:  # pragma: no cover — degraded path
        log.debug("provider_model metadata lookup failed", exc_info=True)
        return None
    if row is None:
        return None
    return row[0] if isinstance(row[0], dict) else None
