"""OpenClaw remote-worker gateway endpoints.

Auth model: every call carries ``X-Api-Key: <raw_key>``. We look the adapter
up by ``sha256(raw_key)`` — no Vault decryption on the hot path. Invalid key
or disabled adapter → 401 with a stable error code.

Three endpoints:

* ``POST /register``     — advertise capabilities, refresh ``last_seen_at``.
* ``POST /poll``         — long-poll for work (``direction=request`` rows).
* ``POST /emit``         — append a ``direction=event`` row, idempotent by
                           ``(run_id, seq)``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, status

from app.api.deps import DBSession
from app.core.errors import Unauthorized
from app.db.models.backend_adapter import BackendAdapter, BackendAdapterHealth
from app.db.session import get_session_factory
from app.schemas.backend import (
    GatewayEmitIn,
    GatewayEmitOut,
    GatewayPollIn,
    GatewayPollMessage,
    GatewayPollOut,
    GatewayRegisterIn,
    GatewayRegisterOut,
)
from app.services import gateway as gw_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/gw/openclaw", tags=["gateway-openclaw"])


async def _current_adapter(
    db: DBSession,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> BackendAdapter:
    """Hot-path authentication for gateway callers.

    We intentionally do NOT surface 404 vs 401 distinctions — mismatched key,
    disabled adapter, and soft-deleted adapter all return the same error.
    """

    if not x_api_key or len(x_api_key) < 16:
        raise Unauthorized("missing_api_key", code="gateway.missing_api_key")
    adapter = await gw_svc.authenticate_adapter(db, raw_api_key=x_api_key)
    if adapter is None:
        raise Unauthorized("invalid_api_key", code="gateway.unauthorized")
    return adapter


@router.post("/register", response_model=GatewayRegisterOut)
async def register_worker(
    body: GatewayRegisterIn,
    db: DBSession,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> GatewayRegisterOut:
    adapter = await _current_adapter(db, x_api_key)
    await gw_svc.mark_seen(
        db,
        adapter=adapter,
        health=BackendAdapterHealth.HEALTHY,
        capabilities=body.capabilities or {},
        endpoint=body.endpoint,
    )
    await db.commit()
    log.info(
        "openclaw.register adapter=%s worker_version=%s",
        adapter.id,
        body.worker_version,
    )
    return GatewayRegisterOut(
        adapter_id=adapter.id,
        adapter_name=adapter.name,
        workspace_id=adapter.workspace_id,
    )


@router.post("/poll", response_model=GatewayPollOut)
async def poll_work(
    body: GatewayPollIn,
    db: DBSession,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> GatewayPollOut:
    adapter = await _current_adapter(db, x_api_key)
    # Release the per-request DB session before entering long-poll so we don't
    # pin a connection for the full wait.
    adapter_id = adapter.id
    await gw_svc.mark_seen(db, adapter=adapter)
    await db.commit()

    rows = await gw_svc.poll_pending_requests(
        adapter_id=adapter_id,
        max_messages=body.max_messages,
        wait_ms=body.wait_ms,
    )

    messages = [
        GatewayPollMessage(
            run_id=r.run_id,
            kind=r.kind,
            session_id=r.session_id,
            agent_id=r.agent_id,
            payload=r.payload_json or {},
            issued_at=r.created_at,
        )
        for r in rows
    ]
    return GatewayPollOut(messages=messages)


@router.post(
    "/emit",
    response_model=GatewayEmitOut,
    status_code=status.HTTP_200_OK,
)
async def emit_event(
    body: GatewayEmitIn,
    request: Request,
    db: DBSession,
    x_api_key: Annotated[str | None, Header(alias="X-Api-Key")] = None,
) -> GatewayEmitOut:
    adapter = await _current_adapter(db, x_api_key)
    await gw_svc.mark_seen(db, adapter=adapter)

    row, duplicated, terminal = await gw_svc.emit_event(
        db,
        adapter=adapter,
        run_id=body.run_id,
        seq=body.seq,
        kind=body.kind,
        data=body.data,
    )
    await db.commit()

    if row is None and not duplicated:
        # Adapter posted to a run_id that either never existed or doesn't
        # belong to it. 401 keeps the error shape uniform with auth failures.
        raise Unauthorized(
            "unknown_run",
            code="gateway.unknown_run",
            extras={"run_id": str(body.run_id)},
        )

    _ = request  # reserved for future audit hook
    return GatewayEmitOut(
        accepted=row is not None,
        duplicated=duplicated,
        run_terminal=terminal,
    )


__all__ = ["router"]


# Helper re-exported for tests.
async def resolve_adapter_from_key(
    raw_api_key: str,
) -> uuid.UUID | None:
    factory = get_session_factory()
    async with factory() as db:
        adapter = await gw_svc.authenticate_adapter(db, raw_api_key=raw_api_key)
        return adapter.id if adapter else None
