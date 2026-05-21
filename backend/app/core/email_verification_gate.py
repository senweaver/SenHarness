"""Block PENDING identities from non-whitelist API routes.

Implemented as a starlette middleware (rather than a per-route Depends)
so future endpoints inherit the gate without churn. The whitelist is a
small set of paths a PENDING user must still reach to complete the
onboarding ritual: ``/me``, ``/auth/verify-email/*``,
``/auth/resend-verification``, ``/auth/logout``.

Performance note: the gate only runs when the request carries an
``Authorization: Bearer …`` header — unauthenticated and webhook traffic
short-circuits with no DB hit. Authenticated requests pay one indexed
``identities`` row lookup; the OK path is a single round-trip with no
cross-tenant joins.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.security import decode_token
from app.db.models.identity import IdentityStatus
from app.db.session import get_session_factory

log = logging.getLogger(__name__)

_PENDING_SAFE_EXACT: frozenset[str] = frozenset(
    {
        "/api/v1/me",
        "/api/v1/auth/logout",
        "/api/v1/auth/resend-verification",
        "/api/v1/auth/registration-mode",
    }
)
_PENDING_SAFE_PREFIXES: tuple[str, ...] = (
    "/api/v1/auth/verify-email",
    "/api/v1/auth/refresh",
    "/api/v1/health",
    "/api/v1/version",
)
_OAUTH_PATH_RE = re.compile(r"^/api/v1/auth/oauth(/|$)")


def is_pending_safe_route(path: str) -> bool:
    """True for endpoints PENDING identities are allowed to reach."""
    if path in _PENDING_SAFE_EXACT:
        return True
    if any(path.startswith(prefix) for prefix in _PENDING_SAFE_PREFIXES):
        return True
    if _OAUTH_PATH_RE.match(path):
        return True
    return not path.startswith("/api/")


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header.split(" ", 1)[1].strip() or None
    return None


class EmailVerificationGateMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if is_pending_safe_route(path):
            return await call_next(request)

        token = _bearer_token(request)
        if not token:
            return await call_next(request)

        try:
            payload = decode_token(token, expected_kind="access")
        except Exception:
            return await call_next(request)

        sub = payload.get("sub")
        if not sub:
            return await call_next(request)

        try:
            from app.repositories.identity import IdentityRepository

            factory = get_session_factory()
            async with factory() as session:
                import uuid

                try:
                    identity_id = uuid.UUID(sub)
                except (TypeError, ValueError):
                    return await call_next(request)
                identity = await IdentityRepository(session).get(identity_id)
                if identity is not None and identity.status == IdentityStatus.PENDING:
                    return JSONResponse(
                        status_code=403,
                        content={
                            "code": "auth.email_unverified",
                            "detail": "email_unverified",
                            "extras": {},
                        },
                    )
        except Exception as exc:
            log.warning("email_verification_gate lookup failed: %s", exc)
            return await call_next(request)

        return await call_next(request)
