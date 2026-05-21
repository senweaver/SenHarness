"""Logging configuration.

* Dev default: rich-ish plain text with timestamps.
* ``LOG_FORMAT=json``: structured single-line JSON suitable for Loki / CloudWatch /
  Datadog. Includes ``trace_id`` / ``span_id`` from the active OTel context when
  tracing is configured.
"""

from __future__ import annotations

import contextlib
import json
import logging
from logging.config import dictConfig
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

from app.core.config import settings

# Channel SDKs that ship their own ``FileHandler`` (botpy drops
# ``botpy.log`` in CWD, etc.). We strip those so all records flow
# through the single configured root pipeline.
_CHATTY_SDK_LOGGERS = ("botpy", "lark_oapi", "dingtalk_stream", "discord")


class JsonFormatter(logging.Formatter):
    """Minimal structured log record. No external dependency."""

    _RESERVED: ClassVar[set[str]] = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # Attach trace/span id from OTel active span if present.
        try:
            from opentelemetry import trace

            span = trace.get_current_span()
            ctx = span.get_span_context() if span is not None else None
            if ctx is not None and ctx.is_valid:
                payload["trace_id"] = format(ctx.trace_id, "032x")
                payload["span_id"] = format(ctx.span_id, "016x")
        except Exception:  # pragma: no cover
            pass

        # Pick up any .extra fields the caller attached.
        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            if k in payload:
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except Exception:
                payload[k] = repr(v)

        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    use_json = settings.LOG_FORMAT == "json"
    fmt = (
        "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
        if settings.APP_DEBUG
        else "%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "text": {"format": fmt, "datefmt": "%H:%M:%S"},
                "json": {"()": JsonFormatter},
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "formatter": "json" if use_json else "text",
                },
            },
            "root": {
                "level": "DEBUG" if settings.APP_DEBUG else "INFO",
                "handlers": ["default"],
            },
            "loggers": {
                "uvicorn.error": {"level": "INFO"},
                "uvicorn.access": {"level": "INFO" if settings.APP_DEBUG else "WARNING"},
                "sqlalchemy.engine": {"level": "WARNING"},
                "websockets": {"level": "WARNING"},
                "websockets.client": {"level": "WARNING"},
                "websockets.protocol": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                "httpcore.http11": {"level": "WARNING"},
                "httpcore.connection": {"level": "WARNING"},
                "httpx": {"level": "WARNING"},
                "urllib3.connectionpool": {"level": "WARNING"},
                "asyncio": {"level": "INFO"},
                "Lark": {"level": "INFO"},
                "lark_oapi": {"level": "INFO"},
                "dingtalk_stream": {"level": "INFO"},
                "botpy": {"level": "INFO"},
                "discord": {"level": "INFO"},
            },
        }
    )

    if settings.LOG_DIR:
        log_dir = Path(settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "backend.log",
            maxBytes=settings.LOG_FILE_MAX_BYTES,
            backupCount=settings.LOG_FILE_BACKUPS,
            encoding="utf-8",
        )
        file_handler.setFormatter(
            JsonFormatter()
            if use_json
            else logging.Formatter(fmt, datefmt="%H:%M:%S")
        )
        logging.getLogger().addHandler(file_handler)

    for name in _CHATTY_SDK_LOGGERS:
        _muzzle_sdk_logger(name)

    logging.captureWarnings(True)


def _muzzle_sdk_logger(name: str) -> None:
    """Strip current ``FileHandler``s from an SDK logger and block future ones.

    Channel SDKs attach their own rotating file handler at their own
    import time, which happens lazily after ``setup_logging()`` has
    run. We override ``addHandler`` on the named logger so any later
    ``FileHandler`` add silently no-ops — records still propagate to
    the root handler chain we configured above.
    """
    sdk_logger = logging.getLogger(name)
    for handler in list(sdk_logger.handlers):
        if isinstance(handler, logging.FileHandler):
            sdk_logger.removeHandler(handler)
            with contextlib.suppress(Exception):
                handler.close()
    sdk_logger.propagate = True

    original_add_handler = sdk_logger.addHandler

    def _filtered_add_handler(handler: logging.Handler) -> None:
        if isinstance(handler, logging.FileHandler):
            with contextlib.suppress(Exception):
                handler.close()
            return
        original_add_handler(handler)

    sdk_logger.addHandler = _filtered_add_handler  # type: ignore[method-assign]  # ty: ignore[invalid-assignment]
