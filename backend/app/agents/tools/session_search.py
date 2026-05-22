"""L2 episodic memory — Postgres full-text search over past messages.

Two surfaces in one tool:

* ``summarize=False`` → the original V2 behaviour, returning ranked raw
  message rows the agent can read inline.
* ``summarize=True`` (default) → after the ts_vector pass we hand the
  top-K hits to the aux LLM (``AuxiliaryTask.SUMMARIZE``) and return a
  ``{summary, bullet_points, evidence_message_ids, raw_results}``
  envelope. The summary text never goes into ``message_history``: the
  pydantic-ai tool result is consumed once by the caller's LLM and then
  dropped, so the agent's prompt cache stays valid for the rest of the
  turn (M0.7 cache-aware-mutation invariant).

Cache-safety + reliability invariants:

* The ts_vector query is workspace-scoped at the SQL layer, so cross-
  workspace leakage is impossible regardless of what the agent passes.
* The aux LLM is shielded by an independent breaker bucket
  (``summarize:fail:<workspace_id>``) so a misbehaving summary model
  cannot trip M0.3's judge breaker or M2.4's verifier breaker.
* When the LLM returns ``evidence_message_ids`` that don't appear in
  the raw hits, the unknown ids are filtered out and an audit row is
  written — the agent gets back only ids it can actually open.
* Any aux failure (no model configured, timeout, parse error) downgrades
  to the raw-results path and writes ``summarize.fallback`` audit so
  operators can spot model outages without the agent run breaking.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.agents.auxiliary_client import (
    AuxiliaryTask,
    call_aux_chat,
    get_aux_model,
    get_workspace_aux_settings,
)
from app.agents.tools._context import get_context
from app.db.session import get_session_factory
from app.jobs._breaker import (
    bump_failure,
    consume_rate,
    is_breaker_open,
    reset_failure,
)
from app.services import audit as audit_svc

log = logging.getLogger(__name__)


# ─── Constants ───────────────────────────────────────────────
SUMMARIZE_BREAKER_BUCKET = "summarize"
SUMMARIZE_RATE_BUCKET = "summarize"

AUDIT_INVOKED = "summarize.invoked"
AUDIT_FALLBACK = "summarize.fallback"
AUDIT_EVIDENCE_FILTERED = "summarize.evidence_filtered"
AUDIT_BREAKER_TRIPPED = "summarize.breaker_tripped"

# Hard cap on the bytes per snippet that go into the aux prompt — keeps
# the summary call O(1) regardless of how chatty individual messages
# were. The raw row body is still trimmed to 800 chars at the wire,
# this cap is only what the aux model sees.
_AUX_SNIPPET_CHARS = 600


# ─── Argument + response schemas ─────────────────────────────
class SessionSearchArgs(BaseModel):
    """Search past messages in the caller's workspace by free-text query."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        validation_alias=AliasChoices("query", "q", "text"),
        description=(
            "Natural-language query. Postgres tsquery semantics — multiple words are "
            "ANDed. Omit operators unless you know tsquery syntax."
        ),
    )
    limit: int = Field(default=10, ge=1, le=50)
    role: str | None = Field(
        default=None,
        description=(
            "Optional filter: 'user' for human turns, 'assistant' for agent "
            "replies. Default returns both."
        ),
    )
    session_id: str | None = Field(
        default=None,
        description=(
            "Optional: restrict to a single session id. Useful when recovering "
            "context from a specific thread the user just referenced."
        ),
    )
    summarize: bool = Field(
        default=True,
        description=(
            "When True (default), the top-K raw hits are passed to the aux LLM "
            "to produce a single distilled answer with cited evidence ids. Set "
            "False to skip the aux call and read raw rows directly."
        ),
    )
    summary_max_chars: int = Field(
        default=800,
        ge=100,
        le=4000,
        description=(
            "Hard upper bound on the summary text the aux LLM may return. "
            "Ignored when summarize=False."
        ),
    )


class SessionSearchSummary(BaseModel):
    """Structured output schema the aux LLM is asked to fill."""

    summary: str = Field(default="", max_length=4000)
    bullet_points: list[str] = Field(default_factory=list, max_length=10)
    evidence_message_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


# ─── Raw search ─────────────────────────────────────────────
async def _run_raw_search(args: SessionSearchArgs, *, workspace_id: uuid.UUID) -> list[dict]:
    """Workspace-scoped tsvector query (the M2.5.8 behaviour the rest of
    the tool builds on). Always returns a serialisable list of dicts.
    """
    factory = get_session_factory()
    params: dict = {
        "ws": str(workspace_id),
        "query": args.query,
        "limit": args.limit,
    }
    where_extras = []
    if args.role in {"user", "assistant", "system", "tool_call", "tool_result"}:
        where_extras.append("m.role = :role")
        params["role"] = args.role
    if args.session_id:
        try:
            params["sid"] = uuid.UUID(args.session_id)
            where_extras.append("m.session_id = :sid")
        except ValueError:
            # Invalid UUIDs are ignored rather than 500 — the LLM sometimes
            # echoes back truncated session ids.
            pass

    extra_sql = (" AND " + " AND ".join(where_extras)) if where_extras else ""

    sql = text(
        f"""
        SELECT
            m.id              AS message_id,
            m.session_id      AS session_id,
            m.role            AS role,
            m.created_at      AS created_at,
            coalesce(m.content_json->>'text', '') AS body,
            s.title           AS session_title,
            ts_rank_cd(
                to_tsvector('simple', coalesce(m.content_json->>'text', '')),
                plainto_tsquery('simple', :query)
            ) AS score
        FROM messages m
        JOIN sessions s ON s.id = m.session_id
        WHERE m.workspace_id = :ws
          AND s.deleted_at IS NULL
          AND to_tsvector('simple', coalesce(m.content_json->>'text', ''))
              @@ plainto_tsquery('simple', :query)
          {extra_sql}
        ORDER BY score DESC, m.created_at DESC
        LIMIT :limit
        """
    )

    async with factory() as db:
        rows = (await db.execute(sql, params)).mappings().all()

    return [
        {
            "message_id": str(r["message_id"]),
            "session_id": str(r["session_id"]),
            "session_title": r["session_title"],
            "role": str(r["role"]),
            "created_at": (
                r["created_at"].isoformat()
                if hasattr(r["created_at"], "isoformat")
                else str(r["created_at"])
            ),
            "score": round(float(r["score"] or 0.0), 4),
            "body": (r["body"] or "")[:800],
        }
        for r in rows
    ]


# ─── Audit helper ───────────────────────────────────────────
def _hash_for_audit(value: str) -> str:
    """Short SHA-256 prefix used in audit metadata so raw query strings
    never end up in log files. 16 hex chars = 64 bits of entropy.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


async def _record_audit(
    *,
    action: str,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    summary_text: str,
    metadata: dict,
) -> None:
    factory = get_session_factory()
    try:
        async with factory() as db:
            await audit_svc.record(
                db,
                action=action,
                actor_identity_id=actor_identity_id,
                workspace_id=workspace_id,
                resource_type="session_search",
                resource_id=None,
                summary=summary_text,
                metadata=metadata,
            )
            await db.commit()
    except Exception:  # pragma: no cover - audit best-effort
        log.exception("session_search audit failed action=%s", action)


# ─── Aux summarisation ──────────────────────────────────────
def _load_summary_prompt() -> str:
    """Load the summary system prompt from disk (cached)."""
    from functools import cache
    from pathlib import Path

    @cache
    def _read() -> str:
        path = Path(__file__).resolve().parent.parent / "templates" / "session_search_summary.md"
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:  # pragma: no cover - dev sanity
            return (
                "Summarise the search hits and return JSON with keys "
                "summary, bullet_points, evidence_message_ids."
            )

    return _read()


def _build_user_prompt(query: str, hits: list[dict]) -> str:
    """Compact JSON payload listing each hit with its message_id.

    Trimming the body in the user prompt (not in the raw hits we return
    to the agent) keeps the summary call cheap without hiding evidence
    from the caller.
    """
    payload = {
        "query": query,
        "results": [
            {
                "message_id": h["message_id"],
                "session_id": h["session_id"],
                "session_title": h.get("session_title"),
                "role": h.get("role"),
                "created_at": h.get("created_at"),
                "score": h.get("score"),
                "snippet": (h.get("body") or "")[:_AUX_SNIPPET_CHARS],
            }
            for h in hits
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def _summarise_hits(
    *,
    workspace_id: uuid.UUID,
    query: str,
    hits: list[dict],
    summary_max_chars: int,
) -> SessionSearchSummary | None:
    """Run a single aux-LLM call. Returns ``None`` on any failure path
    (no model configured, timeout, unparseable shape) — the caller then
    falls back to the raw results.
    """
    factory = get_session_factory()
    async with factory() as db:
        config = await get_aux_model(db, workspace_id=workspace_id, task=AuxiliaryTask.SUMMARIZE)
    if config is None:
        return None

    system = _load_summary_prompt().replace("{max_chars}", str(summary_max_chars))
    user_payload = _build_user_prompt(query, hits)

    try:
        response = await call_aux_chat(
            config=config,
            system=system,
            user=user_payload,
            response_format=SessionSearchSummary,
            timeout_s=20.0,
        )
    except Exception:  # pragma: no cover - call_aux_chat already swallows
        log.exception("session_search summarise raised unexpectedly")
        return None

    if isinstance(response, SessionSearchSummary):
        return response
    return None


# ─── Main entry point ───────────────────────────────────────
async def run_session_search(args: SessionSearchArgs) -> dict:
    """Execute the ts_vector search, then optionally summarise via aux LLM.

    Return shape (cache-aware — none of these fields are persisted into
    ``message_history``; the tool result is consumed once and dropped):

    * ``summarize=False`` or zero hits →
      ``{query, hits: [...], summarized: False}``
    * ``summarize=True`` happy path →
      ``{query, summary, bullet_points, evidence_message_ids,
         based_on_count, raw_results, summarized: True}``
    * ``summarize=True`` fallback (breaker / aux failure) →
      ``{query, hits, summarized: False, fallback_reason: str}``
    """
    ctx = get_context()

    raw_hits = await _run_raw_search(args, workspace_id=ctx.workspace_id)

    if not args.summarize or not raw_hits:
        return {
            "query": args.query,
            "hits": raw_hits,
            "summarized": False,
        }

    workspace_str = str(ctx.workspace_id)

    # ── Per-workspace knobs (rate / breaker thresholds) ───────
    factory = get_session_factory()
    async with factory() as db:
        aux_settings = await get_workspace_aux_settings(db, workspace_id=ctx.workspace_id)
    rate_limit = int(aux_settings.get("summarize_rate_per_minute", 30))
    breaker_strikes = int(aux_settings.get("summarize_fail_strikes", 3))
    breaker_window = int(aux_settings.get("summarize_fail_window_seconds", 300))
    breaker_recover = int(aux_settings.get("summarize_breaker_recover_seconds", 1800))

    # ── Breaker check ────────────────────────────────────────
    breaker_open = await is_breaker_open(
        bucket=SUMMARIZE_BREAKER_BUCKET,
        workspace_id=workspace_str,
        trip_at=breaker_strikes,
    )
    if breaker_open:
        await _record_audit(
            action=AUDIT_FALLBACK,
            workspace_id=ctx.workspace_id,
            actor_identity_id=ctx.identity_id,
            summary_text="session_search summary skipped: breaker open",
            metadata={
                "reason": "breaker_open",
                "hit_count": len(raw_hits),
                "query_hash": _hash_for_audit(args.query),
            },
        )
        return {
            "query": args.query,
            "hits": raw_hits,
            "summarized": False,
            "fallback_reason": "breaker_open",
        }

    # ── Rate gate ───────────────────────────────────────────
    rate_ok = await consume_rate(
        bucket=SUMMARIZE_RATE_BUCKET,
        workspace_id=workspace_str,
        limit=rate_limit,
        period_seconds=60,
    )
    if not rate_ok:
        await _record_audit(
            action=AUDIT_FALLBACK,
            workspace_id=ctx.workspace_id,
            actor_identity_id=ctx.identity_id,
            summary_text="session_search summary skipped: rate limit",
            metadata={
                "reason": "rate_limited",
                "hit_count": len(raw_hits),
                "limit_per_minute": rate_limit,
                "query_hash": _hash_for_audit(args.query),
            },
        )
        return {
            "query": args.query,
            "hits": raw_hits,
            "summarized": False,
            "fallback_reason": "rate_limited",
        }

    # ── Aux LLM call ────────────────────────────────────────
    summary_obj = await _summarise_hits(
        workspace_id=ctx.workspace_id,
        query=args.query,
        hits=raw_hits,
        summary_max_chars=args.summary_max_chars,
    )

    if summary_obj is None:
        strikes = await bump_failure(
            bucket=SUMMARIZE_BREAKER_BUCKET,
            workspace_id=workspace_str,
            window_seconds=breaker_window,
            recover_seconds=breaker_recover,
        )
        if strikes >= breaker_strikes:
            await _record_audit(
                action=AUDIT_BREAKER_TRIPPED,
                workspace_id=ctx.workspace_id,
                actor_identity_id=ctx.identity_id,
                summary_text="session_search summary breaker tripped",
                metadata={
                    "bucket": SUMMARIZE_BREAKER_BUCKET,
                    "strikes": int(strikes),
                    "trip_at": breaker_strikes,
                    "window_seconds": breaker_window,
                    "recover_seconds": breaker_recover,
                },
            )
        await _record_audit(
            action=AUDIT_FALLBACK,
            workspace_id=ctx.workspace_id,
            actor_identity_id=ctx.identity_id,
            summary_text="session_search summary aux call failed",
            metadata={
                "reason": "aux_failure",
                "hit_count": len(raw_hits),
                "strikes": int(strikes),
                "query_hash": _hash_for_audit(args.query),
            },
        )
        return {
            "query": args.query,
            "hits": raw_hits,
            "summarized": False,
            "fallback_reason": "aux_failure",
        }

    # ── Validate evidence ids ⊆ raw hit ids ────────────────
    valid_ids: set[uuid.UUID] = set()
    for hit in raw_hits:
        try:
            valid_ids.add(uuid.UUID(hit["message_id"]))
        except (ValueError, TypeError, KeyError):
            continue

    proposed_ids = list(summary_obj.evidence_message_ids)
    accepted_ids: list[uuid.UUID] = []
    rejected_ids: list[uuid.UUID] = []
    for mid in proposed_ids:
        if mid in valid_ids and mid not in accepted_ids:
            accepted_ids.append(mid)
        elif mid not in valid_ids:
            rejected_ids.append(mid)

    if rejected_ids:
        await _record_audit(
            action=AUDIT_EVIDENCE_FILTERED,
            workspace_id=ctx.workspace_id,
            actor_identity_id=ctx.identity_id,
            summary_text="session_search summary cited unknown message ids",
            metadata={
                "rejected_count": len(rejected_ids),
                "rejected_ids": [str(x) for x in rejected_ids[:10]],
                "accepted_count": len(accepted_ids),
                "hit_count": len(raw_hits),
            },
        )

    # ── Truncate summary to caller's budget ─────────────────
    summary_text = summary_obj.summary or ""
    if len(summary_text) > args.summary_max_chars:
        summary_text = summary_text[: max(1, args.summary_max_chars - 1)] + "…"

    # ── Reset breaker on success + audit invocation ────────
    await reset_failure(bucket=SUMMARIZE_BREAKER_BUCKET, workspace_id=workspace_str)
    await _record_audit(
        action=AUDIT_INVOKED,
        workspace_id=ctx.workspace_id,
        actor_identity_id=ctx.identity_id,
        summary_text=(
            f"session_search summary: {len(accepted_ids)} evidence ids from {len(raw_hits)} hits"
        ),
        metadata={
            "hit_count": len(raw_hits),
            "evidence_count": len(accepted_ids),
            "evidence_filtered": len(rejected_ids),
            "summary_chars": len(summary_text),
            "bullet_count": len(summary_obj.bullet_points),
            "query_hash": _hash_for_audit(args.query),
        },
    )

    return {
        "query": args.query,
        "summary": summary_text,
        "bullet_points": list(summary_obj.bullet_points),
        "evidence_message_ids": [str(x) for x in accepted_ids],
        "based_on_count": len(raw_hits),
        "raw_results": raw_hits,
        "summarized": True,
    }
