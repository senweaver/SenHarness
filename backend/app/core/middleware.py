"""Global middleware: request id, security headers."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import settings
from app.core.prometheus import record_http

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


# ─── Security header constants ───────────────────────────────
# Conservative CSP for an API backend: the browser never navigates directly
# to an API response, so `default-src 'none'` is safe and prevents any sort
# of same-origin script execution surprise (e.g. if someone opens a JSON
# response URL directly). The frontend's own CSP lives in Next.js config.
_API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

# 1 year HSTS with preload-eligible parameters — only enabled when TLS is
# actually on (we infer from JWT_REFRESH_COOKIE_SECURE=true, which mirrors
# the operator's prod posture).
_HSTS = "max-age=31536000; includeSubDomains; preload"

# Permissions-Policy: deny dangerous browser features by default. Individual
# frontend routes can widen these via their own meta tag / response header.
_PERMISSIONS_POLICY = (
    "accelerometer=(), "
    "autoplay=(), "
    "camera=(), "
    "display-capture=(), "
    "geolocation=(), "
    "gyroscope=(), "
    "magnetometer=(), "
    "microphone=(), "
    "midi=(), "
    "payment=(), "
    "picture-in-picture=(), "
    "publickey-credentials-get=(), "
    "screen-wake-lock=(), "
    "usb=(), "
    "xr-spatial-tracking=(), "
    "interest-cohort=()"
)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach/propagate a stable request id for correlation.

    Also folds the request id into the active OTel span attributes and
    Logfire baggage so downstream log records carry the same correlation key.
    Every failure path is best-effort — we never block the request on
    observability plumbing.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        request.state.request_id = rid

        # Best-effort: tag the active OTel span so Jaeger/Tempo/Logfire can
        # filter by request_id. `opentelemetry` is a soft dep; when missing,
        # the attach/update is silently skipped.
        import contextlib

        with contextlib.suppress(Exception):
            from opentelemetry import trace as _ot_trace

            span = _ot_trace.get_current_span()
            if span is not None and span.is_recording():
                span.set_attribute("senharness.request_id", rid)

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = rid
            return response
        finally:
            try:
                record_http(
                    method=request.method,
                    # Keep path cardinality bounded — strip trailing slash and
                    # skip query-strings so Prometheus label count is sane.
                    path=request.url.path.rstrip("/") or "/",
                    status=status_code,
                    duration_s=time.perf_counter() - started,
                )
            except Exception:
                pass


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Conservative browser-security defaults.

    Tightened in V1 to cover the OWASP ASVS headers checklist:

        * Content-Security-Policy (API-shape, deny-all)
        * Strict-Transport-Security (only when TLS is on)
        * Cross-Origin-Opener-Policy
        * Cross-Origin-Embedder-Policy
        * Cross-Origin-Resource-Policy
        * Referrer-Policy
        * X-Content-Type-Options
        * X-Frame-Options
        * Permissions-Policy (camera/mic/geolocation/... denied)

    Note: WebSocket upgrades don't carry security headers in the same way,
    but Starlette's HTTP response path is the only thing this middleware
    touches, so WS is unaffected.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        resp = await call_next(request)

        # X-headers: universal, no tradeoffs
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        resp.headers.setdefault("Permissions-Policy", _PERMISSIONS_POLICY)

        # CSP: tight default for JSON responses. The /docs and /redoc pages
        # need script-src for Swagger UI, so we skip those paths.
        path = request.url.path
        if not (path.startswith("/docs") or path.startswith("/redoc")):
            resp.headers.setdefault("Content-Security-Policy", _API_CSP)

        # HSTS: only in production + TLS deployments. Dev http://localhost
        # must not have HSTS or Safari caches the forced-https for months.
        if (
            str(settings.APP_ENV).lower() == "production"
            and settings.JWT_REFRESH_COOKIE_SECURE
        ):
            resp.headers.setdefault("Strict-Transport-Security", _HSTS)

        # Cross-origin isolation headers. COOP protects window.opener access
        # across tabs; COEP requires cross-origin resources to opt-in via
        # CORP (we set this on our own responses). For an API backend the
        # tradeoff is zero — we never embed third-party content.
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        # COEP would break OAuth popup flows where the browser loads the IdP
        # response into the opener window, so we use the relaxed variant.
        resp.headers.setdefault(
            "Cross-Origin-Embedder-Policy", "credentialless"
        )

        return resp
