"""Global exception handlers: return consistent `{code, detail, extras}` envelope."""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import AppError

log = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.code, "detail": exc.detail, "extras": exc.extras},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "http.error" if exc.status_code >= 500 else "http.client_error"
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": code, "detail": exc.detail, "extras": {}},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = jsonable_encoder(
            exc.errors(),
            custom_encoder={Exception: str},
        )
        return JSONResponse(
            status_code=422,
            content={
                "code": "app.validation_failed",
                "detail": "validation_failed",
                "extras": {"errors": errors},
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "code": "app.internal_error",
                "detail": "internal_error",
                "extras": {"traceback": traceback.format_exc()} if settings.APP_DEBUG else {},
            },
        )
