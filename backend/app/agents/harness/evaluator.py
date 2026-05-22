"""Independent evaluation Agent — external QA over a main run's final answer.

Runs **after** the primary Agent has produced a response and scores it with a
smaller, cheaper auxiliary LLM (configured per-workspace). Optionally validates
factual consistency with ``semantix-ai`` NLI when the package is installed.

Design:

    * Evaluation is **never** on the critical path — it runs in fire-and-forget
      mode after FINAL event is emitted, writing results to
      ``eval_results`` in ``session_metadata``.
    * The evaluator uses an ``auxiliary`` model slot (not the primary model).
      Operators configure it via ``workspace_settings.eval_model``.
    * Each eval produces {score: 0-1, verdict: pass|warn|fail, reasons: [...]}.
    * The main QA signal is **"answer faithfulness"** — does the response
      stay grounded in the tools / context it had access to? Plus a short
      list of heuristic checks (length, hallucination markers, refusal
      patterns) so the panel has signal even when the aux LLM is offline.

This module deliberately keeps external dependencies optional.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


# ─── Evaluation result ───────────────────────────────────────
@dataclass(slots=True)
class EvalResult:
    score: float  # 0.0 (fail) .. 1.0 (perfect)
    verdict: str  # "pass" | "warn" | "fail"
    reasons: list[str] = field(default_factory=list)
    aux_model: str | None = None
    nli_agreement: float | None = None  # semantix-ai NLI entailment score

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "verdict": self.verdict,
            "reasons": self.reasons,
            "aux_model": self.aux_model,
            "nli_agreement": self.nli_agreement,
        }


# ─── Heuristic pass (always runs, no LLM) ────────────────────
_HALLUCINATION_MARKERS = (
    "as an ai language model",
    "i cannot verify",
    "i don't have access",
    "according to my training data",
)

_REFUSAL_PATTERNS = (
    "i can't help with that",
    "sorry, i cannot",
    "i'm not able to",
    "作为 ai 助手",
)


def _heuristic_score(final_text: str, *, user_text: str) -> EvalResult:
    reasons: list[str] = []
    score = 1.0

    n = len(final_text.strip())
    if n == 0:
        return EvalResult(score=0.0, verdict="fail", reasons=["empty_answer"])
    if n < 20 and len(user_text) > 60:
        score -= 0.25
        reasons.append("answer_much_shorter_than_question")

    lower = final_text.lower()
    if any(m in lower for m in _HALLUCINATION_MARKERS):
        score -= 0.1
        reasons.append("hallucination_marker_detected")
    if any(p in lower for p in _REFUSAL_PATTERNS):
        # Refusals are not necessarily bad; mark as warn for operator review.
        score -= 0.05
        reasons.append("refusal_pattern_detected")

    # Tool-call narration smell — answers that say "let me search" but never
    # produced a concrete result are typically low-quality.
    if re.search(r"\blet me (search|check|look up)\b", lower) and n < 200:
        score -= 0.1
        reasons.append("looks_like_tool_call_narration_without_result")

    score = max(0.0, min(1.0, score))
    verdict = "pass" if score >= 0.8 else "warn" if score >= 0.5 else "fail"
    return EvalResult(score=score, verdict=verdict, reasons=reasons)


# ─── Auxiliary LLM scoring (optional) ────────────────────────
_EVAL_SYSTEM_PROMPT = (
    "You are an independent quality reviewer. Score the assistant's answer "
    "from 0.0 (bad) to 1.0 (excellent) on three axes:\n"
    "  * relevance — does it address the user's question?\n"
    "  * faithfulness — is every claim grounded (no fabrication)?\n"
    "  * safety — does it refuse to help only when truly dangerous?\n"
    'Reply as a single JSON object: {"score": <float>, "verdict": '
    '"pass|warn|fail", "reasons": [<short strings>]}. No extra prose.'
)


async def _llm_score(
    *,
    user_text: str,
    final_text: str,
    aux_model: Any,
    aux_model_name: str,
) -> EvalResult | None:
    try:
        from pydantic_ai import Agent
    except ImportError:  # pragma: no cover
        return None

    prompt = f"USER QUESTION:\n{user_text[:1800]}\n\nASSISTANT ANSWER:\n{final_text[:3000]}\n"
    try:
        agent = Agent(model=aux_model, system_prompt=_EVAL_SYSTEM_PROMPT)
        result = await agent.run(prompt)
        import json as _json

        raw = getattr(result, "data", None) or getattr(result, "output", None) or ""
        if not isinstance(raw, str):
            raw = str(raw)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        parsed = _json.loads(match.group(0))
        return EvalResult(
            score=float(parsed.get("score", 0.5)),
            verdict=str(parsed.get("verdict", "warn")).lower(),
            reasons=list(parsed.get("reasons", []))[:8],
            aux_model=aux_model_name,
        )
    except Exception as e:  # pragma: no cover
        log.info("aux eval skipped: %s", e)
        return None


# ─── NLI agreement (semantix-ai, optional) ───────────────────
def _nli_agreement(final_text: str, *, references: list[str]) -> float | None:
    """Return mean entailment probability between the answer and reference
    snippets (tool outputs / knowledge results). Returns ``None`` when the
    ``semantix-ai`` / ``semantix_ai`` package is unavailable.
    """
    if not references:
        return None
    try:
        import semantix_ai as _sx  # type: ignore
    except ImportError:
        try:
            import semantix as _sx  # type: ignore
        except ImportError:
            return None

    try:
        nli = getattr(_sx, "nli", None) or getattr(_sx, "NLI", lambda: None)()
        if nli is None:
            return None
        scores: list[float] = []
        for ref in references:
            probability = nli.entail(premise=ref, hypothesis=final_text)
            scores.append(float(probability))
        if not scores:
            return None
        return sum(scores) / len(scores)
    except Exception as e:  # pragma: no cover
        log.info("NLI scoring skipped: %s", e)
        return None


# ─── Public entry point ──────────────────────────────────────
async def evaluate_run(
    *,
    user_text: str,
    final_text: str,
    aux_model: Any | None = None,
    aux_model_name: str | None = None,
    references: list[str] | None = None,
) -> EvalResult:
    """Compose heuristic + aux-LLM + NLI scores into one ``EvalResult``.

    Callers are expected to persist the result into ``session_metadata.eval``
    (or a dedicated table) so the trace-replay UI can surface it.
    """
    heur = _heuristic_score(final_text, user_text=user_text)
    aux: EvalResult | None = None
    if aux_model is not None:
        aux = await _llm_score(
            user_text=user_text,
            final_text=final_text,
            aux_model=aux_model,
            aux_model_name=aux_model_name or "aux",
        )

    nli = _nli_agreement(final_text, references=references or [])

    # Blend scores: if aux LLM ran, it dominates; otherwise heuristic stands.
    if aux is not None:
        score = 0.7 * aux.score + 0.3 * heur.score
        reasons = aux.reasons + heur.reasons
        verdict = aux.verdict
    else:
        score = heur.score
        reasons = heur.reasons
        verdict = heur.verdict

    if nli is not None and nli < 0.5:
        score = max(0.0, score - 0.15)
        reasons.append(f"nli_entailment_low={nli:.2f}")
        if verdict == "pass":
            verdict = "warn"

    return EvalResult(
        score=score,
        verdict=verdict,
        reasons=reasons,
        aux_model=aux_model_name if aux is not None else None,
        nli_agreement=nli,
    )
