"""L2 episodic memory — Postgres full-text search over messages (V2).

Queries the tsvector GIN index created in migration 0022. Results are
ranked by ``ts_rank_cd`` and decorated with session title + timestamp so
the Agent can reconstruct the episode context ("you told me last
Tuesday about the Q3 OKR planning call").

Workspace-scoped; cross-workspace leakage is impossible at the DB query
level because we always filter on ``messages.workspace_id = :ws``.
"""

from __future__ import annotations

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.agents.tools._context import get_context
from app.db.session import get_session_factory


class SessionSearchArgs(BaseModel):
    """Search past messages in the caller's workspace by free-text query."""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(
        ...,
        min_length=1,
        validation_alias=AliasChoices("query", "q", "text"),
        description=(
            "Natural-language query. Postgres tsquery semantics — multiple words are "
            "ANDed. Omit operators unless you know tsquery syntax."
        ),
    )
    limit: int = Field(default=8, ge=1, le=30)
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


async def run_session_search(args: SessionSearchArgs) -> dict:
    ctx = get_context()
    factory = get_session_factory()

    # Use ``plainto_tsquery`` so the LLM can pass unquoted natural
    # language without learning tsquery syntax. ``simple`` dictionary
    # (no stemming) works for both English and Chinese when combined
    # with the ``to_tsvector('simple', ...)`` index in migration 0022.
    params: dict = {
        "ws": str(ctx.workspace_id),
        "query": args.query,
        "limit": args.limit,
    }
    where_extras = []
    if args.role in {"user", "assistant", "system", "tool_call", "tool_result"}:
        where_extras.append("m.role = :role")
        params["role"] = args.role
    if args.session_id:
        # Invalid UUIDs are ignored rather than 500 — the LLM sometimes
        # echoes back truncated session ids.
        import uuid

        try:
            params["sid"] = uuid.UUID(args.session_id)
            where_extras.append("m.session_id = :sid")
        except ValueError:
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

    return {
        "query": args.query,
        "hits": [
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
        ],
    }
