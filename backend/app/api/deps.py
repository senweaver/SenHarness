"""FastAPI dependency helpers (DB session, current identity/workspace, roles)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDenied, Unauthorized
from app.core.security import decode_token
from app.db.session import get_session_factory

_bearer_scheme = HTTPBearer(auto_error=False)


async def db_session() -> AsyncIterator[AsyncSession]:
    """Per-request AsyncSession."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


DBSession = Annotated[AsyncSession, Depends(db_session)]


async def current_identity_id(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> uuid.UUID:
    if creds is None or creds.scheme.lower() != "bearer":
        raise Unauthorized("missing_bearer_token", code="auth.missing_token")
    payload = decode_token(creds.credentials, expected_kind="access")
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as e:
        raise Unauthorized("invalid_subject", code="auth.invalid_subject") from e


CurrentIdentityId = Annotated[uuid.UUID, Depends(current_identity_id)]


async def current_workspace_id(
    request: Request,
    x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> uuid.UUID | None:
    """Active workspace: `X-Workspace-Id` header wins, else JWT `ws` claim."""
    if x_workspace_id:
        try:
            return uuid.UUID(x_workspace_id)
        except ValueError as e:
            raise Unauthorized("invalid_workspace_id", code="auth.invalid_workspace") from e
    creds = request.headers.get("authorization", "")
    if creds.lower().startswith("bearer "):
        try:
            payload = decode_token(creds.split(" ", 1)[1], expected_kind="access")
        except Unauthorized:
            return None
        ws = payload.get("ws")
        if ws:
            try:
                return uuid.UUID(ws)
            except ValueError:
                return None
    return None


CurrentWorkspaceId = Annotated[uuid.UUID | None, Depends(current_workspace_id)]


def require_roles(*required: str):
    """Dependency factory: raise `PermissionDenied` if JWT roles don't intersect required."""

    async def _check(request: Request) -> None:
        creds = request.headers.get("authorization", "")
        if not creds.lower().startswith("bearer "):
            raise Unauthorized("missing_bearer_token", code="auth.missing_token")
        payload = decode_token(creds.split(" ", 1)[1], expected_kind="access")
        roles = set(payload.get("roles") or [])
        if not roles.intersection(required):
            raise PermissionDenied("role_missing", code="auth.role_missing", extras={"required": list(required)})

    return _check
