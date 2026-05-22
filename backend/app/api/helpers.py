"""Pure-function helpers reused across API routes.

Kept separate from ``app.api.v1.*`` routers so unit tests can import them
without pulling in SQLAlchemy / pgvector / FastAPI routing. Anything here
must stay free of database / framework imports.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import HTTPException, status

log = logging.getLogger(__name__)


# ─── Webhook ingress ─────────────────────────────────────────
INGRESS_TOKEN_HEADER: Final[str] = "X-Senharness-Token"


def resolve_ingress_token(header_token: str | None, query_token: str | None) -> str:
    """Webhook token resolver — prefer header, fall back to query.

    Tokens in the URL leak into proxy access logs, browser history and
    APM samples; providers still sending ``?token=...`` get a single
    deprecation warning per request so operators can migrate at pace.

    Raises ``HTTPException(401)`` when neither source has a token.
    """
    if header_token:
        return header_token
    if query_token:
        log.warning(
            "hook ingress token supplied via query string — please migrate "
            "the provider to %s header (tokens in URLs leak into access logs).",
            INGRESS_TOKEN_HEADER,
        )
        return query_token
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": "hooks.missing_token",
            "message": (
                f"Supply the shared secret via {INGRESS_TOKEN_HEADER} header "
                "(or, during migration, the ?token= query param)."
            ),
        },
    )


# ─── OAuth `next` redirect sanitiser ─────────────────────────
def sanitize_next_path(raw: str | None) -> str:
    """Accept only same-origin, relative ``next`` targets after OAuth login.

    Rejects:
      * absolute URLs (``http://evil.com``) — would open-redirect
      * protocol-relative URLs (``//evil.com/path``) — the browser resolves
        these as cross-origin too
      * UNC / backslash paths (``\\\\evil.com``) — Windows edge case
      * paths whose first segment contains a scheme colon
        (``/javascript:alert(1)``)

    Falls back to ``/`` whenever the input fails any check. Query strings
    are preserved on legitimate paths because the frontend uses them for
    deep-linking state.
    """
    if not raw:
        return "/"
    if not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or raw.startswith("/\\") or raw.startswith("\\"):
        return "/"
    first_segment = raw.split("/", 2)[1] if len(raw) > 1 else ""
    if ":" in first_segment:
        return "/"
    return raw
