"""FastAPI application factory + lifespan."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.admin import setup_admin
from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import setup_logging
from app.core.middleware import RequestIDMiddleware, SecurityHeadersMiddleware
from app.core.observability import instrument_fastapi, setup_observability
from app.security.keyring import ensure_master_key_on_startup

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    setup_observability()

    # Refuse to boot in production with dev-default credentials — the
    # failure mode is silent compromise, so we make it loud at startup.
    problems = settings.check_production_secrets()
    if problems:
        for p in problems:
            log.critical("production secret guard: %s", p)
        raise RuntimeError(
            "Refusing to start: production secret guard tripped. Fix the "
            "above errors or set APP_ENV=development for local testing."
        )

    ensure_master_key_on_startup()
    # Importing triggers registry.register(...) side-effect for every built-in
    # backend. Second one is D18's OpenClaw remote gateway.
    import app.agents.kernels.native
    import app.agents.kernels.openclaw  # noqa: F401

    log.info("SenHarness %s starting (env=%s)", __version__, settings.APP_ENV)
    try:
        yield
    finally:
        log.info("SenHarness shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version=__version__,
        docs_url="/docs" if settings.APP_DEBUG else None,
        redoc_url="/redoc" if settings.APP_DEBUG else None,
        openapi_url="/openapi.json" if settings.APP_DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    # Short-lived server session for OAuth CSRF state + SQLAdmin auth.
    # Same signing key as JWT keeps us from shipping yet another secret.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.JWT_SECRET_KEY,
        session_cookie="sh_srv_session",
        max_age=60 * 60,
        same_site="lax",
        https_only=settings.JWT_REFRESH_COOKIE_SECURE,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    app.include_router(api_router, prefix=settings.API_PREFIX)

    register_exception_handlers(app)

    # Internal-ops DB browser at /admin/sql/. Mounts SessionMiddleware itself
    # via SQLAdmin's Auth flow; safely no-ops when sqladmin isn't installed.
    setup_admin(app)

    # Attach OTel auto-instrumentation (FastAPI routes + SQLAlchemy queries).
    # Must run AFTER the router is wired so excluded_urls match correctly.
    # Safe no-op when no tracing exporter is configured.
    instrument_fastapi(app)

    return app


app = create_app()


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:  # pragma: no cover - fallback
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "detail": exc.detail, "extras": exc.extras},
    )
