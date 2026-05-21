"""Observability: Logfire + OpenTelemetry + Langfuse bootstrap (best-effort).

All hooks are feature-flagged via environment variables and fail open: if a
dependency is missing or a remote endpoint misbehaves, the app still boots.

Enabled by:

* ``LOGFIRE_TOKEN``                              — Pydantic Logfire SaaS
* ``OTEL_EXPORTER_OTLP_ENDPOINT``                — generic OTLP/HTTP (Tempo,
                                                  Jaeger, Honeycomb, ...)
* ``LANGFUSE_PUBLIC_KEY`` + ``LANGFUSE_SECRET_KEY`` (+ optional ``LANGFUSE_HOST``)
                                                  — Langfuse.com or self-hosted

Auto-instruments FastAPI requests and SQLAlchemy queries when a tracer
provider is active.
"""

from __future__ import annotations

import base64
import logging
import os

from fastapi import FastAPI

from app import __version__
from app.core.config import settings

log = logging.getLogger(__name__)

_TRACING_ACTIVE = False


def _configure_logfire() -> None:
    if not settings.LOGFIRE_TOKEN:
        return
    try:
        import logfire

        logfire.configure(
            token=settings.LOGFIRE_TOKEN,
            service_name=settings.APP_NAME,
            environment=settings.APP_ENV,
        )
        try:
            logfire.instrument_pydantic()
        except Exception:  # pragma: no cover
            pass
        log.info("Logfire observability enabled")
    except Exception as e:  # pragma: no cover
        log.warning("Logfire init failed: %s", e)


def _configure_otlp() -> None:
    """Wire a TracerProvider with whichever OTLP endpoints are configured.

    Multiple exporters are stacked: a generic ``OTEL_EXPORTER_OTLP_ENDPOINT``
    plus Langfuse's dedicated ``/api/public/otel`` can both be active.
    """
    global _TRACING_ACTIVE

    otlp_endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    lf_pub = settings.LANGFUSE_PUBLIC_KEY
    lf_sec = settings.LANGFUSE_SECRET_KEY
    lf_host = settings.LANGFUSE_HOST or "https://cloud.langfuse.com"

    if not (otlp_endpoint or (lf_pub and lf_sec)):
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": settings.APP_NAME,
                    "deployment.environment": settings.APP_ENV,
                    "service.version": __version__,
                }
            )
        )

        if otlp_endpoint:
            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
            )
            log.info("OTLP span exporter configured: %s", otlp_endpoint)

        if lf_pub and lf_sec:
            # Langfuse accepts OTLP/HTTP at /api/public/otel with Basic auth
            # (public:secret). See https://langfuse.com/docs/opentelemetry.
            auth = base64.b64encode(f"{lf_pub}:{lf_sec}".encode()).decode()
            lf_endpoint = f"{lf_host.rstrip('/')}/api/public/otel/v1/traces"
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=lf_endpoint,
                        headers={"Authorization": f"Basic {auth}"},
                    )
                )
            )
            log.info("Langfuse OTLP exporter configured: %s", lf_host)

        trace.set_tracer_provider(provider)
        _TRACING_ACTIVE = True
    except Exception as e:  # pragma: no cover
        log.warning("OTel init failed: %s", e)


def _enable_sentry() -> None:
    if not settings.SENTRY_DSN:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.APP_ENV,
            release=__version__,
            traces_sample_rate=0.1,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
        )
        log.info("Sentry SDK initialized")
    except Exception as e:  # pragma: no cover
        log.warning("Sentry init failed: %s", e)


def setup_observability() -> None:
    """Invoked once from the FastAPI lifespan startup."""
    # Stash APP_VERSION as an env var so Logfire/OTel pick it up automatically
    # even if the caller doesn't pass it.
    os.environ.setdefault("OTEL_SERVICE_NAME", settings.APP_NAME)

    _configure_logfire()
    _configure_otlp()
    _enable_sentry()


def instrument_fastapi(app: FastAPI) -> None:
    """Attach OTel instrumentation to a FastAPI app + the default SA engine.

    Idempotent and safe to call even when tracing is disabled — we check the
    module-level flag set by :func:`setup_observability`.
    """
    if not _TRACING_ACTIVE and not settings.LOGFIRE_TOKEN:
        return

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,version,openapi.json,docs,redoc",
        )
        log.info("FastAPI auto-instrumentation attached")
    except Exception as e:  # pragma: no cover
        log.warning("FastAPIInstrumentor attach failed: %s", e)

    try:
        from opentelemetry.instrumentation.sqlalchemy import (
            SQLAlchemyInstrumentor,
        )

        from app.db.session import get_engine

        SQLAlchemyInstrumentor().instrument(engine=get_engine().sync_engine)
        log.info("SQLAlchemy auto-instrumentation attached")
    except Exception as e:  # pragma: no cover
        log.warning("SQLAlchemyInstrumentor attach failed: %s", e)


def tracing_active() -> bool:
    """Public read-only flag — ``True`` if any OTLP exporter was configured."""
    return _TRACING_ACTIVE
