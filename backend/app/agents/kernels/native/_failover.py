"""Provider-failover orchestration for the native runner (M2.5.3).

Lives next to ``runner.py`` so the chain wrapper can share its private
helpers without polluting the public ``app.services`` surface. The
runner picks one of two execution paths inside ``_run_inner``:

* ``failover_enabled = False`` → ``_pydantic_ai_stream`` is invoked
  exactly as before, byte-for-byte. The chain wrapper is **never**
  imported on this path so its existence cannot regress the unscoped
  runner code.
* ``failover_enabled = True`` → :func:`run_with_failover` iterates the
  resolved chain and invokes ``_pydantic_ai_stream`` once per attempt.
  Each attempt receives the same ``message_history`` + ``user_prompt``
  so the upstream prompt cache prefix stays stable across providers.

Failover semantics
------------------

The wrapper only fails over when:

1. The provider raised a *retryable* exception
   (:func:`app.services.provider_health.is_retryable_failure`).
2. No streamable output frame (DELTA / TOOL_CALL / TOOL_RESULT /
   FINAL) has been emitted to the client yet.

Once a frame has reached the WebSocket the user has already observed
partial output; replaying with the next provider would either confuse
them with a second attempt or break the prompt-cache invariant by
modifying the visible turn. We surface the failure as the ``ERROR`` +
``FINAL`` pair the inner stream would have emitted on its own.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agents.kernels.base import RunEvent, RunEventKind, RunRequest
from app.agents.kernels.model_client import (
    ResolvedModel,
    build_pydantic_ai_model,
    parse_override,
    resolve_for_agent,
)
from app.services import audit as audit_svc
from app.services import provider_health as health_svc
from app.services.notification_events import emit_event
from app.services.provider_chain import (
    ProviderChainEntry,
    ProviderFailoverConfig,
)

log = logging.getLogger(__name__)

__all__ = [
    "AllProvidersUnavailable",
    "ProviderFailoverHint",
    "build_pydantic_ai_model_from_entry",
    "run_with_failover",
]


# ─── Typed signals ─────────────────────────────────────────
class ProviderFailoverHint(Exception):
    """Raised by ``_pydantic_ai_stream`` (when called with
    ``raise_provider_errors=True``) to ask the chain wrapper to try
    the next entry. Carries the original failure for audit.
    """

    def __init__(
        self,
        *,
        original: BaseException,
        failure_kind: str,
    ) -> None:
        super().__init__(repr(original))
        self.original = original
        self.failure_kind = failure_kind


class AllProvidersUnavailable(Exception):
    """Raised when every entry in the chain produced a retryable failure.

    The chain wrapper translates this into a structured ``ERROR`` +
    ``FINAL`` pair before the runner's outer ``except`` would have
    swallowed it; carrying the per-attempt failure list makes the
    audit row self-describing.
    """

    def __init__(self, attempts: list[dict[str, Any]]) -> None:
        super().__init__(f"all {len(attempts)} provider attempts failed")
        self.attempts = attempts


# ─── Per-attempt state ──────────────────────────────────────
@dataclass(slots=True)
class _AttemptOutcome:
    entry: ProviderChainEntry
    failure_kind: str | None
    cooldown_started: bool
    success: bool


# ─── Public entry point ─────────────────────────────────────
async def run_with_failover(
    req: RunRequest,
    *,
    primary_resolved: ResolvedModel,
    primary_model: Any,
    served_name: str,
    chain: list[ProviderChainEntry],
    config: ProviderFailoverConfig,
    redis: Any | None,
    inner_stream: Callable[..., AsyncIterator[RunEvent]],
) -> AsyncIterator[RunEvent]:
    """Iterate the chain, forwarding events from the first attempt that
    produces visible output or completes successfully.

    Args:
        primary_resolved / primary_model: the resolver's chosen primary
            (already built once by the caller). Reused on the first
            attempt when its ``provider_kind`` + ``model_name`` match
            chain entry 0; otherwise we rebuild from the chain entry.
        chain: parsed + cooldown-filtered list of candidates. MUST be
            non-empty; the caller short-circuits the empty case.
        inner_stream: the runner's ``_pydantic_ai_stream`` (passed in
            so this module never imports back into ``runner`` and
            avoids a circular import).
    """
    if not chain:
        raise AllProvidersUnavailable(attempts=[])

    attempts: list[dict[str, Any]] = []

    for idx, entry in enumerate(chain):
        resolved, model = await _resolve_for_attempt(
            req=req,
            entry=entry,
            attempt_index=idx,
            primary_resolved=primary_resolved,
            primary_model=primary_model,
        )
        if model is None:
            attempts.append(
                {
                    "provider_kind": entry.provider_kind,
                    "model_id": entry.model_id,
                    "failure_kind": "model_build_failed",
                    "attempt_index": idx,
                }
            )
            continue

        outcome = _AttemptOutcome(
            entry=entry,
            failure_kind=None,
            cooldown_started=False,
            success=False,
        )

        # Track whether anything has reached the client; once a frame
        # is emitted we can no longer safely fail over (the user has
        # observed partial output).
        emitted_visible = False
        try:
            async for ev in inner_stream(
                req,
                model=model,
                resolved=resolved,
                served_name=served_name,
                raise_provider_errors=True,
            ):
                if ev.kind in _VISIBLE_FRAMES:
                    emitted_visible = True
                yield ev
            outcome.success = True
        except ProviderFailoverHint as hint:
            outcome.failure_kind = hint.failure_kind
            log.info(
                "provider_failover attempt %d/%d failed kind=%s "
                "provider=%s model=%s",
                idx + 1,
                len(chain),
                hint.failure_kind,
                entry.provider_kind,
                entry.model_id,
            )
        except Exception:
            # Non-retryable provider exception → propagate so the
            # inner stream's outer ``except`` (which surfaced ERROR +
            # FINAL frames the wrapper just yielded) can finish the
            # run cleanly. We did not yield via ``raise_provider_errors``
            # so there is nothing to replay.
            raise

        if outcome.success:
            await health_svc.record_success(
                redis,
                provider_kind=entry.provider_kind,
                model_id=entry.model_id,
            )
            attempts.append(
                {
                    "provider_kind": entry.provider_kind,
                    "model_id": entry.model_id,
                    "failure_kind": None,
                    "attempt_index": idx,
                    "success": True,
                }
            )
            await _audit_chain_outcome(
                req=req,
                attempts=attempts,
                served_name=served_name,
                final_entry=entry,
            )
            return

        # Attempt failed — bump health + audit + maybe trip cooldown +
        # decide whether to keep going.
        snapshot = await health_svc.record_failure(
            redis,
            provider_kind=entry.provider_kind,
            model_id=entry.model_id,
            failure_kind=outcome.failure_kind or health_svc.FailureKind.OTHER,
            cooldown_threshold=config.cooldown_threshold,
            cooldown_seconds=config.cooldown_seconds,
        )
        outcome.cooldown_started = bool(
            snapshot.extras.get("cooldown_just_started")
        )
        attempts.append(
            {
                "provider_kind": entry.provider_kind,
                "model_id": entry.model_id,
                "failure_kind": outcome.failure_kind,
                "attempt_index": idx,
                "success": False,
                "cooldown_started": outcome.cooldown_started,
            }
        )
        await _audit_failover_attempted(
            req=req,
            from_entry=entry,
            to_entry=chain[idx + 1] if idx + 1 < len(chain) else None,
            failure_kind=outcome.failure_kind or health_svc.FailureKind.OTHER,
            attempt_index=idx,
            served_name=served_name,
        )
        if outcome.cooldown_started:
            await _audit_cooldown_started(
                req=req,
                entry=entry,
                cooldown_seconds=config.cooldown_seconds,
                served_name=served_name,
            )
            await _emit_cooldown_admin_alert(
                req=req,
                entry=entry,
                cooldown_seconds=config.cooldown_seconds,
            )

        # Once any visible frame went out we cannot replay; stop the
        # chain even if more entries remain. The inner stream's last
        # ERROR/FINAL pair already reached the client.
        if emitted_visible:
            log.warning(
                "provider_failover skipping remaining chain entries "
                "(%d) because visible frames already emitted",
                len(chain) - idx - 1,
            )
            raise AllProvidersUnavailable(attempts=attempts)

    # Chain fully exhausted with no success.
    await _audit_failover_exhausted(
        req=req,
        attempts=attempts,
        served_name=served_name,
    )
    raise AllProvidersUnavailable(attempts=attempts)


_VISIBLE_FRAMES = frozenset(
    {
        RunEventKind.DELTA,
        RunEventKind.TOOL_CALL,
        RunEventKind.TOOL_RESULT,
        RunEventKind.FINAL,
    }
)


# ─── Per-attempt model resolution ───────────────────────────
async def _resolve_for_attempt(
    *,
    req: RunRequest,
    entry: ProviderChainEntry,
    attempt_index: int,
    primary_resolved: ResolvedModel,
    primary_model: Any,
) -> tuple[ResolvedModel, Any]:
    """Reuse the primary model when it matches entry 0; otherwise rebuild.

    Reusing the primary on entry 0 saves one DB round-trip + one
    pydantic-ai provider construction on the happy path (chain[0] is
    the same provider the resolver picked anyway).
    """
    if (
        attempt_index == 0
        and primary_resolved.provider_kind == entry.provider_kind
        and primary_resolved.model_name == entry.model_id
    ):
        return primary_resolved, primary_model

    resolved = await resolve_for_agent(
        workspace_id=req.workspace_id,
        agent_id=req.agent_id,
        override=entry.upstream_label,
    )
    if resolved is None:
        # Fall back to a pure parsed override — no DB credentials, but
        # at least the runner attempts to build something. The model
        # build will fail and the wrapper records ``model_build_failed``.
        parsed = parse_override(entry.upstream_label) or ResolvedModel(
            provider_kind=entry.provider_kind,
            model_name=entry.model_id,
            api_key=None,
            source="override",
        )
        return parsed, None
    model = build_pydantic_ai_model(resolved)
    return resolved, model


def build_pydantic_ai_model_from_entry(entry: ProviderChainEntry) -> tuple[
    ResolvedModel, Any
]:
    """Synchronous helper for tests that don't need DB credentials.

    Returns ``(resolved, None)`` when the model build fails so callers
    can branch on the second element exactly as the runtime path does.
    """
    parsed = parse_override(entry.upstream_label) or ResolvedModel(
        provider_kind=entry.provider_kind,
        model_name=entry.model_id,
        api_key=None,
        source="override",
    )
    return parsed, build_pydantic_ai_model(parsed)


# ─── Audit + notification helpers ───────────────────────────
async def _audit_failover_attempted(
    *,
    req: RunRequest,
    from_entry: ProviderChainEntry,
    to_entry: ProviderChainEntry | None,
    failure_kind: str,
    attempt_index: int,
    served_name: str,
) -> None:
    metadata = {
        "run_id": str(req.run_id),
        "from_provider": from_entry.provider_kind,
        "from_model": from_entry.model_id,
        "to_provider": to_entry.provider_kind if to_entry else None,
        "to_model": to_entry.model_id if to_entry else None,
        "failure_kind": failure_kind,
        "attempt_index": attempt_index,
        "served_model_name": served_name,
    }
    await _safe_audit(
        action="provider.failover_attempted",
        req=req,
        summary=(
            f"failover from {from_entry.upstream_label} → "
            f"{to_entry.upstream_label if to_entry else '∅'} "
            f"after {failure_kind}"
        ),
        metadata=metadata,
    )


async def _audit_failover_exhausted(
    *,
    req: RunRequest,
    attempts: list[dict[str, Any]],
    served_name: str,
) -> None:
    await _safe_audit(
        action="provider.failover_exhausted",
        req=req,
        summary=(
            f"all {len(attempts)} provider attempts failed for "
            f"served={served_name}"
        ),
        metadata={
            "run_id": str(req.run_id),
            "served_model_name": served_name,
            "attempts": attempts,
        },
    )


async def _audit_chain_outcome(
    *,
    req: RunRequest,
    attempts: list[dict[str, Any]],
    served_name: str,
    final_entry: ProviderChainEntry,
) -> None:
    """Audit ``provider.failover_succeeded`` only when ≥1 prior attempt
    failed — when the first attempt succeeded the chain wrapper was a
    no-op and the existing ``provider.upstream_called`` row is enough.
    """
    failed_attempts = [a for a in attempts if not a.get("success")]
    if not failed_attempts:
        return
    await _safe_audit(
        action="provider.failover_succeeded",
        req=req,
        summary=(
            f"recovered on {final_entry.upstream_label} after "
            f"{len(failed_attempts)} failed attempts"
        ),
        metadata={
            "run_id": str(req.run_id),
            "served_model_name": served_name,
            "final_provider": final_entry.provider_kind,
            "final_model": final_entry.model_id,
            "attempts": attempts,
        },
    )


async def _audit_cooldown_started(
    *,
    req: RunRequest,
    entry: ProviderChainEntry,
    cooldown_seconds: int,
    served_name: str,
) -> None:
    await _safe_audit(
        action="provider.cooldown_started",
        req=req,
        summary=(
            f"{entry.upstream_label} entered cooldown "
            f"({cooldown_seconds}s)"
        ),
        metadata={
            "run_id": str(req.run_id),
            "served_model_name": served_name,
            "provider_kind": entry.provider_kind,
            "model_id": entry.model_id,
            "cooldown_seconds": cooldown_seconds,
        },
    )


async def _safe_audit(
    *,
    action: str,
    req: RunRequest,
    summary: str,
    metadata: dict[str, Any],
) -> None:
    """Open a short-lived DB session, write the audit row, commit.

    The chain wrapper is on the hot path of every failover-enabled
    turn; surfacing an audit hiccup as a hard failure would defeat
    the purpose of failover. We log + swallow.
    """
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as fresh:
            await audit_svc.record(
                fresh,
                action=action,
                actor_identity_id=req.identity_id,
                workspace_id=req.workspace_id,
                resource_type="agent",
                resource_id=req.agent_id,
                summary=summary,
                metadata=metadata,
            )
            await fresh.commit()
    except Exception:  # pragma: no cover — defensive
        log.warning("audit %s failed run=%s", action, req.run_id, exc_info=True)


async def _emit_cooldown_admin_alert(
    *,
    req: RunRequest,
    entry: ProviderChainEntry,
    cooldown_seconds: int,
) -> None:
    """Best-effort M0.10 fan-out for the workspace_admins audience."""
    try:
        from app.db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as fresh:
            await emit_event(
                fresh,
                event_key="provider.cooldown_admin_alert",
                workspace_id=req.workspace_id,
                actor_identity_id=req.identity_id,
                payload={
                    "provider_kind": entry.provider_kind,
                    "model_id": entry.model_id,
                    "cooldown_seconds": cooldown_seconds,
                    "resource_type": "agent",
                    "resource_id": str(req.agent_id),
                },
                cooldown_resource_id=f"{entry.provider_kind}:{entry.model_id}",
            )
            await fresh.commit()
    except Exception:  # pragma: no cover — defensive
        log.warning(
            "provider.cooldown_admin_alert emit failed run=%s",
            req.run_id,
            exc_info=True,
        )


# Silence unused imports kept for the type signature surface.
_ = (Awaitable, uuid)
