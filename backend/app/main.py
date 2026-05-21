"""FastAPI application factory + lifespan."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app import __version__
from app.admin import setup_admin
from app.api.exception_handlers import register_exception_handlers
from app.api.router import api_router
from app.core.config import settings
from app.core.email_verification_gate import EmailVerificationGateMiddleware
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
    # Warn when legacy LLM/search ``*_API_KEY`` env vars are still present.
    # Business credentials moved to vault-backed Settings → Providers in v2;
    # leftover env vars are no longer read but indicate a stale .env that
    # should be cleaned up (the operator may have copy-pasted secrets that
    # are now sitting in plaintext on disk for no reason).
    import os

    import app.agents.kernels.native
    import app.agents.kernels.openclaw  # noqa: F401

    leftover = [k for k in settings.LEGACY_ENV_KEYS if os.environ.get(k)]
    if leftover:
        log.warning(
            "Legacy LLM/search env vars detected (%s). Business-level keys "
            "are now stored in Vault via Settings → Providers; please remove "
            "these from .env to avoid plaintext secrets on disk.",
            ", ".join(leftover),
        )

    log.info("SenHarness %s starting (env=%s)", __version__, settings.APP_ENV)

    # Optional: run the IM stream supervisor in-process. Default ON for
    # single-worker / docker-compose dev; flip ``CHANNEL_RUNTIME_INPROCESS``
    # off in production multi-worker deploys and start a dedicated
    # ``python -m cli.commands channels run`` process instead.
    runtime = None
    if settings.CHANNEL_RUNTIME_INPROCESS:
        try:
            from app.services.channel_runtime import get_runtime

            runtime = get_runtime()
            await runtime.start_all()
        except Exception:  # pragma: no cover - never block API boot on stream errors
            log.exception("ChannelRuntime failed to start; continuing without streams")
            runtime = None

    # M2.5.2 — recover top-level runs orphaned by the previous backend
    # incarnation. Cheap SELECT + UPDATE; runs once per process boot.
    # Failures here must NOT block the API from coming up — a degraded
    # recovery path is better than a stuck container.
    try:
        from app.db.session import get_session_factory as _factory_for_recover
        from app.services import inflight_run as _inflight_svc

        recover_factory = _factory_for_recover()
        async with recover_factory() as fresh:
            recovery = await _inflight_svc.recover_inflight_runs(fresh)
            await fresh.commit()
            log.info("inflight_runs recovery: %s", recovery)
    except Exception:  # pragma: no cover - never block API boot on recovery
        log.exception("inflight_runs recovery hook failed")

    # M0.13 unified platform settings:
    #   1) bootstrap from .env on first start (idempotent)
    #   2) reload the email transport so SMTP-enabled deploys swap off
    #      the LogEmailTransport without a manual API call
    #   3) subscribe to the Redis invalidation channel so other workers
    #      converge on writes within ~5 seconds
    try:
        from app.db.session import get_session_factory
        from app.services import platform_settings as ps_svc
        from app.services.email_transport import (
            reload_email_transport_from_settings,
        )

        factory = get_session_factory()
        async with factory() as fresh:
            seeded = await ps_svc.bootstrap_from_env_if_empty(fresh)
            if seeded:
                log.info("platform_settings: bootstrapped from env: %s", seeded)
            kind = await reload_email_transport_from_settings(fresh)
            log.info("platform_settings: email transport active = %s", kind)
        await ps_svc.start_invalidation_listener()
    except Exception:  # pragma: no cover - never block API boot on settings
        log.exception("platform_settings: startup hook failed")

    # Plugin Host Wiring (M2.5.5) + signature gate (M3.9). Default-
    # deny: ``platform_settings.plugins.allow_user_plugins=False``
    # short-circuits the loader before any disk read. When the master
    # switch is on, the loader runs the M3.9 evaluation per discovered
    # plugin (sha256 + ed25519 signature + PluginRegistry approval) so
    # only signed + admin-approved code makes it onto the runner's
    # hook fan-out. ``allow_unapproved_plugins=True`` is the dev-mode
    # escape — see ``docs/extensions-and-governance.md`` (Plugin host).
    try:
        from pathlib import Path

        from app.db.session import get_session_factory
        from app.services import platform_settings as ps_svc
        from app.services.plugin_loader import load_and_register_plugins

        factory = get_session_factory()
        async with factory() as fresh:
            plugins_settings = await ps_svc.get_section(
                fresh, section=ps_svc.PlatformSettingsSection.PLUGINS
            )
            allow = bool(getattr(plugins_settings, "allow_user_plugins", False))
            plugin_dir = Path(settings.STORAGE_LOCAL_PATH) / "plugins"
            if not allow:
                # Pass the explicit ``False`` so the loader emits the
                # ``plugin.disabled_by_platform_setting`` audit row
                # without reading any plugin folder. Equivalent to the
                # M2.5.5 default-deny path.
                await load_and_register_plugins(
                    fresh,
                    plugin_dir=plugin_dir,
                    allow_user_plugins=False,
                )
                log.debug("plugin_loader: disabled by platform setting")
            else:
                # ``allow_user_plugins=None`` triggers the M3.9
                # signature + approval pipeline. The loader reads
                # platform_settings itself per plugin so the trust
                # root and dev-mode flag are picked up consistently
                # with the admin console.
                loaded = await load_and_register_plugins(
                    fresh, plugin_dir=plugin_dir
                )
                log.info("plugin_loader: %d plugin(s) loaded", len(loaded))
    except Exception:  # pragma: no cover - never block API boot on plugin host
        log.exception("plugin_loader: startup hook failed")

    # Cold-start mitigation: warm the pydantic-ai model build cache so the
    # first chat turn doesn't eat the import + provider-construction tax.
    # Scheduled as a background task so HTTP routes accept traffic the
    # moment a single provider warms; the budget is enforced inside the
    # task itself.
    warmup_task: asyncio.Task[None] | None = None
    try:
        from app.agents.kernels.warmup import warm_model_clients

        warmup_task = asyncio.create_task(warm_model_clients())
    except Exception:  # pragma: no cover - never block API boot on warmup
        log.exception("model warmup: failed to schedule")

    # DB pool heartbeat: Docker Desktop's localhost port proxy on Windows
    # tears down idle TCP sockets after ~30s, so the next acquire pays a
    # full reconnect (~1.5s observed). Keep one connection in regular use
    # so the pool always has at least one recently-active socket to hand
    # out — every other acquire then reuses LIFO and stays warm too.
    db_heartbeat_task: asyncio.Task[None] | None = None

    async def _db_pool_heartbeat() -> None:
        from sqlalchemy import text as _sqltext

        from app.db.session import get_session_factory

        factory = get_session_factory()
        while True:
            try:
                async with factory() as db:
                    await db.execute(_sqltext("SELECT 1"))
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                log.debug("db pool heartbeat tick failed", exc_info=True)
            try:
                await asyncio.sleep(20.0)
            except asyncio.CancelledError:
                raise

    try:
        db_heartbeat_task = asyncio.create_task(
            _db_pool_heartbeat(), name="db-pool-heartbeat"
        )
    except Exception:  # pragma: no cover
        log.exception("db pool heartbeat: failed to schedule")

    try:
        yield
    finally:
        if warmup_task is not None and not warmup_task.done():
            warmup_task.cancel()
            with suppress(asyncio.CancelledError, Exception):  # pragma: no cover
                await warmup_task
        if db_heartbeat_task is not None and not db_heartbeat_task.done():
            db_heartbeat_task.cancel()
            with suppress(asyncio.CancelledError, Exception):  # pragma: no cover
                await db_heartbeat_task
        if runtime is not None:
            try:
                await asyncio.wait_for(
                    runtime.stop_all(),
                    timeout=settings.CHANNEL_RUNTIME_STOP_TIMEOUT_S + 2.0,
                )
            except TimeoutError:
                log.warning(
                    "ChannelRuntime.stop_all exceeded %.1fs; continuing "
                    "shutdown so the worker can recycle",
                    settings.CHANNEL_RUNTIME_STOP_TIMEOUT_S + 2.0,
                )
            except Exception:  # pragma: no cover
                log.exception("ChannelRuntime shutdown error")
        try:
            from app.services import platform_settings as ps_svc

            await ps_svc.stop_invalidation_listener()
        except Exception:  # pragma: no cover
            log.exception("platform_settings: listener shutdown error")

        # Drain & detach the loop's default ThreadPoolExecutor. asyncio's
        # built-in ``shutdown_default_executor`` joins every worker thread
        # with a 300s default timeout, which freezes Windows ``--reload``
        # for 5 minutes when channel SDKs (botpy / dingtalk-stream /
        # wechat ilink) leave in-flight blocking work behind. We cancel
        # queued futures, then null out ``_default_executor`` so asyncio's
        # later shutdown short-circuits and the process can exit promptly.
        try:
            loop = asyncio.get_running_loop()
            executor = getattr(loop, "_default_executor", None)
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)
                loop._default_executor = None  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover - defensive
            log.exception("default executor shutdown error")

        log.info("SenHarness shutting down")

        # Last-resort: arm a daemon timer that force-exits the worker
        # if non-daemon SDK threads (botpy / dingtalk-stream / etc.)
        # hold the process alive past the grace period. Without this
        # the uvicorn worker can stay pinned for 5+ minutes in main-
        # thread teardown while every tenant's traffic stalls.
        grace = float(settings.WORKER_FORCE_EXIT_GRACE_S)
        if grace > 0:
            def _force_exit() -> None:
                os._exit(0)

            timer = threading.Timer(grace, _force_exit)
            timer.daemon = True
            timer.start()


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
    # Default-deny PENDING identities outside the whitelist (M0.9). The
    # gate looks up identity status only when an Authorization header is
    # present so unauthenticated and webhook traffic stays free.
    app.add_middleware(EmailVerificationGateMiddleware)
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
