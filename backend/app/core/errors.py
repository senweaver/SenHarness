"""Typed business errors. All carry a stable `code` usable for i18n lookup."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class AppError(HTTPException):
    """Base application error with a machine-readable `code`."""

    code: str = "app.error"
    default_status: int = status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code or self.default_status, detail=detail or self.code)
        self.code = code or self.code
        self.extras = extras or {}


class NotFound(AppError):
    code = "app.not_found"
    default_status = status.HTTP_404_NOT_FOUND


class PermissionDenied(AppError):
    code = "app.permission_denied"
    default_status = status.HTTP_403_FORBIDDEN


class Unauthorized(AppError):
    code = "app.unauthorized"
    default_status = status.HTTP_401_UNAUTHORIZED


class Conflict(AppError):
    code = "app.conflict"
    default_status = status.HTTP_409_CONFLICT


class ValidationFailed(AppError):
    code = "app.validation_failed"
    default_status = status.HTTP_422_UNPROCESSABLE_ENTITY


class RateLimited(AppError):
    code = "app.rate_limited"
    default_status = status.HTTP_429_TOO_MANY_REQUESTS


class ServiceUnavailable(AppError):
    code = "app.service_unavailable"
    default_status = status.HTTP_503_SERVICE_UNAVAILABLE
