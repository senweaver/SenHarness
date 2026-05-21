"""Cross-platform logical thread service (M3.6).

Implements the four contract calls the dispatcher and the API layer
share:

* :func:`get_routing_config` — resolves the effective routing policy
  for a workspace, platform-default + workspace override merged.
* :func:`find_or_create_thread_for_inbound` — main dispatcher entry.
  Returns ``None`` when ``cross_platform_enabled`` is False so the
  caller falls back to the legacy per-channel routing path.
* :func:`initiate_pairing` / :func:`consume_pairing_code` — the
  6-digit cross-platform pairing handshake. Codes are stored only in
  Redis with a 10-minute TTL by default.
* :func:`list_threads_for_identity` / :func:`get_thread` /
  :func:`relabel_thread` / :func:`unbind_channel` — read+manage
  surface for the cross-platform settings page.

All audit rows live under the ``thread.*`` family for grep-ability.
The service does **not** commit; the route layer (or dispatcher
caller) owns the transaction.
"""

from __future__ import annotations

import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError, NotFound
from app.db.models.logical_thread import LogicalThread, ThreadChannelBinding
from app.db.models.session import Session as SessionModel
from app.db.models.session import SessionKind
from app.repositories.logical_thread import (
    LogicalThreadRepository,
    ThreadChannelBindingRepository,
)
from app.repositories.session import SessionRepository
from app.repositories.workspace import WorkspaceRepository
from app.services import audit as audit_svc
from app.services.system_settings import (
    SystemSettingKey,
    get_system_setting,
)

log = logging.getLogger(__name__)


# ── Errors ──────────────────────────────────────────────
class CrossPlatformDisabled(AppError):  # noqa: N818
    code = "thread.cross_platform_disabled"
    default_status = 400


class PairingCodeInvalid(AppError):  # noqa: N818
    code = "thread.pairing_code_invalid"
    default_status = 400


class PairingCodeExpired(AppError):  # noqa: N818
    code = "thread.pairing_code_expired"
    default_status = 400


class PairingTargetMismatch(AppError):  # noqa: N818
    code = "thread.pairing_target_mismatch"
    default_status = 400


class PairingSourceMissing(AppError):  # noqa: N818
    code = "thread.pairing_source_missing"
    default_status = 400


# ── Routing config resolver ─────────────────────────────
@dataclass(frozen=True, slots=True)
class RoutingConfig:
    cross_platform_enabled: bool
    pairing_required: bool
    pairing_code_ttl_seconds: int
    default_strategy: str


_DEFAULT_TTL_SECONDS = 600


async def get_routing_config(
    db: AsyncSession, *, workspace_id: uuid.UUID
) -> RoutingConfig:
    """Merge platform default + workspace override.

    Workspace ``home_config_json["session_routing"]`` overrides each
    field independently. Missing fields back-fill from the platform
    layer; the dispatcher always sees a fully-populated dataclass.
    """
    platform = await get_system_setting(
        db, SystemSettingKey.SESSION_ROUTING_DEFAULTS, default={}
    )
    if not isinstance(platform, dict):
        platform = {}

    workspace = await WorkspaceRepository(db).get(workspace_id)
    workspace_routing: dict[str, Any] = {}
    if workspace is not None:
        cfg = workspace.home_config_json or {}
        if isinstance(cfg, dict):
            block = cfg.get("session_routing")
            if isinstance(block, dict):
                workspace_routing = block

    merged: dict[str, Any] = {**platform, **workspace_routing}
    return RoutingConfig(
        cross_platform_enabled=bool(merged.get("cross_platform_enabled", False)),
        pairing_required=bool(
            merged.get("pairing_required_for_cross_platform", True)
        ),
        pairing_code_ttl_seconds=int(
            merged.get("pairing_code_ttl_seconds", _DEFAULT_TTL_SECONDS)
        ),
        default_strategy=str(merged.get("default_strategy", "per_channel")),
    )


# ── Inbound dispatch ────────────────────────────────────
async def find_or_create_thread_for_inbound(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID | None,
    agent_id: uuid.UUID,
    channel_id: uuid.UUID,
    external_user_id: str,
    title_hint: str | None = None,
) -> tuple[LogicalThread, SessionModel, bool] | None:
    """Resolve the LogicalThread + Session for an inbound IM message.

    Returns ``None`` when the workspace has not opted in or when the
    dispatcher cannot anchor a thread (no identity, no existing
    binding) — the caller must fall back to the legacy per-channel
    routing path. Returns a tuple ``(thread, session, is_new)``
    otherwise.

    An existing binding is honoured even when ``identity_id`` is None
    at call time: the binding row was created via the pairing flow
    while the user was authenticated, so it is a safe anchor on its
    own. Only the *create* path needs an identity, because we cannot
    invent one.
    """
    cfg = await get_routing_config(db, workspace_id=workspace_id)
    if not cfg.cross_platform_enabled:
        return None

    binding_repo = ThreadChannelBindingRepository(db)
    thread_repo = LogicalThreadRepository(db)

    binding = await binding_repo.get_by_channel_user(
        workspace_id=workspace_id,
        channel_id=channel_id,
        external_user_id=external_user_id,
    )

    if binding is not None:
        thread = await thread_repo.get(binding.thread_id)
        if thread is not None and thread.deleted_at is None:
            session_obj = await SessionRepository(db).get(thread.primary_session_id)
            if session_obj is not None and session_obj.deleted_at is None:
                binding.last_seen_at = _utcnow()
                thread.last_activity_at = _utcnow()
                await db.flush()
                return thread, session_obj, False

    if identity_id is None:
        return None

    session_obj = await SessionRepository(db).create(
        workspace_id=workspace_id,
        kind=SessionKind.CHANNEL,
        subject_id=agent_id,
        channel_id=channel_id,
        owner_identity_id=identity_id,
        title=title_hint,
        metadata_json={"thread_origin": "logical"},
    )
    await db.flush()

    thread = await thread_repo.create(
        workspace_id=workspace_id,
        identity_id=identity_id,
        agent_id=agent_id,
        primary_session_id=session_obj.id,
        last_activity_at=_utcnow(),
    )
    await db.flush()

    await binding_repo.create(
        workspace_id=workspace_id,
        thread_id=thread.id,
        channel_id=channel_id,
        external_user_id=external_user_id,
        last_seen_at=_utcnow(),
        is_paired=False,
    )

    await audit_svc.record(
        db,
        action="thread.created",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="logical_thread",
        resource_id=thread.id,
        summary=f"thread auto-created for {channel_id}/{_redact(external_user_id)}",
        metadata={
            "thread_id": str(thread.id),
            "agent_id": str(agent_id),
            "channel_id": str(channel_id),
            "session_id": str(session_obj.id),
        },
    )
    await audit_svc.record(
        db,
        action="thread.binding_created",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="logical_thread",
        resource_id=thread.id,
        summary="binding created",
        metadata={
            "thread_id": str(thread.id),
            "channel_id": str(channel_id),
            "external_user_hash": _redact(external_user_id),
            "is_paired": False,
        },
    )

    return thread, session_obj, True


# ── Pairing helpers ─────────────────────────────────────
_PAIRING_KEY_PREFIX = "thread:pair:"


def _pairing_key(workspace_id: uuid.UUID, code: str) -> str:
    return f"{_PAIRING_KEY_PREFIX}{workspace_id}:{code}"


def _generate_code() -> str:
    """Six-digit numeric code, drawn from a CSPRNG."""
    return f"{secrets.randbelow(1_000_000):06d}"


async def initiate_pairing(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    source_channel_id: uuid.UUID | None,
    source_external_user_id: str | None,
    target_channel_id: uuid.UUID | None,
    target_external_user_id: str | None,
) -> dict[str, Any]:
    """Issue a 6-digit pairing code stored in Redis with TTL.

    The payload is JSON containing both binding sides so the consumer
    can refuse codes redeemed against a different binding pair. Codes
    expire after :attr:`RoutingConfig.pairing_code_ttl_seconds`
    (default 10 minutes).
    """
    cfg = await get_routing_config(db, workspace_id=workspace_id)
    if not cfg.cross_platform_enabled:
        raise CrossPlatformDisabled(
            "cross_platform_disabled",
            extras={"workspace_id": str(workspace_id)},
        )

    code = _generate_code()
    ttl = max(60, int(cfg.pairing_code_ttl_seconds))
    expires_at = _utcnow() + timedelta(seconds=ttl)
    payload = {
        "workspace_id": str(workspace_id),
        "identity_id": str(identity_id),
        "source": {
            "channel_id": str(source_channel_id) if source_channel_id else None,
            "external_user_id": source_external_user_id,
        },
        "target": {
            "channel_id": str(target_channel_id) if target_channel_id else None,
            "external_user_id": target_external_user_id,
        },
        "expires_at": expires_at.isoformat(),
    }

    try:
        from app.core.rate_limit import get_redis

        r = get_redis()
        await r.set(_pairing_key(workspace_id, code), json.dumps(payload), ex=ttl)
    except Exception:  # pragma: no cover - fail-loud only
        log.exception(
            "thread.pairing: redis unreachable (workspace=%s)", workspace_id
        )
        raise

    await audit_svc.record(
        db,
        action="thread.pairing_code_issued",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="thread_pairing",
        resource_id=None,
        summary="pairing code issued",
        metadata={
            "ttl_seconds": ttl,
            "source_channel_id": str(source_channel_id) if source_channel_id else None,
            "target_channel_id": str(target_channel_id) if target_channel_id else None,
            "source_external_user_hash": _redact(source_external_user_id),
            "target_external_user_hash": _redact(target_external_user_id),
        },
    )

    return {
        "code": code,
        "expires_at": expires_at,
        "ttl_seconds": ttl,
    }


async def consume_pairing_code(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    code: str,
    channel_id: uuid.UUID | None,
    external_user_id: str | None,
) -> dict[str, Any]:
    """Validate the code against the stored payload and merge bindings.

    On success both bindings flip to ``is_paired=True`` and (when
    they pointed at distinct threads) the *target* thread's bindings
    are reassigned to the *source* thread; the orphaned thread is
    soft-deleted with audit ``thread.merged``.

    The *source* binding (whoever issued the code) must already exist
    — pairing is "merge two known threads", not "create a thread on
    spec". The *target* binding (the consumer) may be brand new; if
    the target side hasn't sent an inbound yet, this method creates a
    binding pointed at the source thread so the next inbound lands on
    the merged thread directly.
    """
    cfg = await get_routing_config(db, workspace_id=workspace_id)
    if not cfg.cross_platform_enabled:
        raise CrossPlatformDisabled("cross_platform_disabled")
    if not code.isdigit() or len(code) != 6:
        raise PairingCodeInvalid("pairing_code_invalid")

    try:
        from app.core.rate_limit import get_redis

        r = get_redis()
        raw = await r.get(_pairing_key(workspace_id, code))
    except Exception:
        log.exception(
            "thread.pairing.consume: redis unreachable (workspace=%s)", workspace_id
        )
        raise

    if raw is None:
        await audit_svc.record(
            db,
            action="thread.pairing_code_expired",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="thread_pairing",
            resource_id=None,
            summary="pairing code missing or expired",
            metadata={"channel_id": str(channel_id) if channel_id else None},
        )
        raise PairingCodeExpired("pairing_code_expired")

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except Exception as exc:  # pragma: no cover - defensive
        raise PairingCodeInvalid("pairing_code_invalid") from exc

    if str(payload.get("workspace_id")) != str(workspace_id):
        raise PairingCodeInvalid("pairing_code_invalid")

    target = payload.get("target") or {}
    target_channel_id = target.get("channel_id")
    target_external_user_id = target.get("external_user_id")
    if target_channel_id is not None and (
        str(channel_id) if channel_id else None
    ) != target_channel_id:
        raise PairingTargetMismatch("pairing_target_mismatch")
    if target_external_user_id is not None and target_external_user_id != external_user_id:
        raise PairingTargetMismatch("pairing_target_mismatch")

    binding_repo = ThreadChannelBindingRepository(db)
    thread_repo = LogicalThreadRepository(db)

    source = payload.get("source") or {}
    source_channel_uuid = (
        uuid.UUID(source["channel_id"]) if source.get("channel_id") else None
    )
    target_channel_uuid = (
        uuid.UUID(target_channel_id) if target_channel_id else None
    )

    source_binding = await binding_repo.get_by_channel_user(
        workspace_id=workspace_id,
        channel_id=source_channel_uuid,
        external_user_id=source.get("external_user_id"),
    )
    if source_binding is None:
        raise PairingSourceMissing("pairing_source_missing")

    target_binding = await binding_repo.get_by_channel_user(
        workspace_id=workspace_id,
        channel_id=channel_id if channel_id is not None else target_channel_uuid,
        external_user_id=external_user_id
        if external_user_id is not None
        else target_external_user_id,
    )

    bindings_paired = 0
    threads_merged = 0
    primary_thread_id = source_binding.thread_id

    if not source_binding.is_paired:
        source_binding.is_paired = True
        bindings_paired += 1

    if target_binding is None:
        await binding_repo.create(
            workspace_id=workspace_id,
            thread_id=primary_thread_id,
            channel_id=channel_id if channel_id is not None else target_channel_uuid,
            external_user_id=external_user_id
            if external_user_id is not None
            else target_external_user_id,
            is_paired=True,
            last_seen_at=_utcnow(),
        )
        bindings_paired += 1
    else:
        if not target_binding.is_paired:
            target_binding.is_paired = True
            bindings_paired += 1
        if target_binding.thread_id != primary_thread_id:
            target_thread = await thread_repo.get(target_binding.thread_id)
            if target_thread is not None:
                target_bindings_all = await binding_repo.list_for_thread(
                    workspace_id=workspace_id, thread_id=target_thread.id
                )
                for b in target_bindings_all:
                    b.thread_id = primary_thread_id
                target_thread.deleted_at = _utcnow()
                threads_merged += 1

    primary_thread = await thread_repo.get(primary_thread_id)
    if primary_thread is not None:
        primary_thread.last_activity_at = _utcnow()
    primary_session_id = (
        primary_thread.primary_session_id if primary_thread else None
    )

    try:
        from app.core.rate_limit import get_redis as _get_redis

        await (_get_redis()).delete(_pairing_key(workspace_id, code))
    except Exception:  # pragma: no cover
        pass

    await audit_svc.record(
        db,
        action="thread.pairing_code_consumed",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="logical_thread",
        resource_id=primary_thread_id,
        summary="pairing code consumed",
        metadata={
            "thread_id": str(primary_thread_id),
            "bindings_paired": bindings_paired,
            "threads_merged": threads_merged,
            "channel_id": str(channel_id) if channel_id else None,
            "external_user_hash": _redact(external_user_id),
        },
    )
    if bindings_paired:
        await audit_svc.record(
            db,
            action="thread.binding_paired",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="logical_thread",
            resource_id=primary_thread_id,
            summary=f"{bindings_paired} binding(s) flipped to paired",
            metadata={"thread_id": str(primary_thread_id)},
        )
    if threads_merged:
        await audit_svc.record(
            db,
            action="thread.merged",
            actor_identity_id=identity_id,
            workspace_id=workspace_id,
            resource_type="logical_thread",
            resource_id=primary_thread_id,
            summary=f"{threads_merged} thread(s) merged into primary",
            metadata={"thread_id": str(primary_thread_id)},
        )

    return {
        "thread_id": primary_thread_id,
        "primary_session_id": primary_session_id,
        "bindings_paired": bindings_paired,
        "threads_merged": threads_merged,
    }


# ── Read + manage helpers ───────────────────────────────
async def list_threads_for_identity(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[LogicalThread], int]:
    repo = LogicalThreadRepository(db)
    items = list(
        await repo.list_for_identity(
            workspace_id=workspace_id,
            identity_id=identity_id,
            limit=limit,
            offset=offset,
        )
    )
    total = await repo.count(
        workspace_id=workspace_id,
        identity_id=identity_id,
    )
    return items, total


async def get_thread(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> LogicalThread:
    repo = LogicalThreadRepository(db)
    thread = await repo.get(thread_id)
    if (
        thread is None
        or thread.deleted_at is not None
        or thread.workspace_id != workspace_id
        or thread.identity_id != identity_id
    ):
        raise NotFound("thread_not_found", code="thread.not_found")
    return thread


async def get_bindings_for_thread(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> list[ThreadChannelBinding]:
    return list(
        await ThreadChannelBindingRepository(db).list_for_thread(
            workspace_id=workspace_id, thread_id=thread_id
        )
    )


async def relabel_thread(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    thread_id: uuid.UUID,
    label: str | None,
) -> LogicalThread:
    thread = await get_thread(
        db,
        workspace_id=workspace_id,
        identity_id=identity_id,
        thread_id=thread_id,
    )
    thread.label = label
    await db.flush()
    return thread


async def unbind_channel(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    thread_id: uuid.UUID,
    binding_id: uuid.UUID,
) -> None:
    thread = await get_thread(
        db,
        workspace_id=workspace_id,
        identity_id=identity_id,
        thread_id=thread_id,
    )
    binding_repo = ThreadChannelBindingRepository(db)
    binding = await binding_repo.get(binding_id)
    if (
        binding is None
        or binding.deleted_at is not None
        or binding.thread_id != thread.id
        or binding.workspace_id != workspace_id
    ):
        raise NotFound("binding_not_found", code="thread.binding_not_found")
    binding.deleted_at = _utcnow()
    await audit_svc.record(
        db,
        action="thread.binding_unbinded",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="logical_thread",
        resource_id=thread.id,
        summary="binding removed",
        metadata={
            "thread_id": str(thread.id),
            "binding_id": str(binding.id),
            "channel_id": str(binding.channel_id) if binding.channel_id else None,
        },
    )


async def get_active_session(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    identity_id: uuid.UUID,
    thread_id: uuid.UUID,
) -> SessionModel:
    thread = await get_thread(
        db,
        workspace_id=workspace_id,
        identity_id=identity_id,
        thread_id=thread_id,
    )
    session_obj = await SessionRepository(db).get(thread.primary_session_id)
    if session_obj is None or session_obj.deleted_at is not None:
        raise NotFound("session_missing", code="thread.session_missing")
    return session_obj


# ── Helpers ─────────────────────────────────────────────
def _utcnow() -> datetime:
    """Naive UTC matches the rest of the codebase."""
    return datetime.now(UTC).replace(tzinfo=None)


def _redact(value: str | None) -> str | None:
    """Stable short hash for audit metadata.

    Audit rows must never carry a raw ``external_user_id`` for GDPR
    reasons; a 12-hex SHA-256 prefix is enough to correlate across
    audit ticks but unlinkable without the original.
    """
    if value is None:
        return None
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
