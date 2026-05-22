"""Cold-start mitigation: pre-build pydantic-ai Models at app boot.

Building the first ``OpenAIChatModel`` / ``AnthropicModel`` / etc. in a
fresh worker is dominated by two costs:

  1. First-time import of ``pydantic_ai.models.openai`` and its
     transitive deps (anywhere from 500ms to 3s on cold disk + AV).
  2. Provider-class construction that lazily wires an ``httpx.AsyncClient``
     and on cross-continental hosts pays a 5-10s DNS + TLS handshake on
     the very first request.

Folding (1) into application startup is essentially free and saves
every first-chat across every workspace from eating the import bill.
(2) is partially mitigated: the model lives in
:data:`app.agents.kernels.model_client._MODEL_BUILD_CACHE` so the
synchronous construction path is amortised; the actual handshake is
still deferred to the first outbound request, but at least the
import-blocked Python work is already done.

The warm-up is best-effort, capped by a wall-clock budget so a
single mis-configured workspace can't stall API boot, and runs
asynchronously inside the lifespan so HTTP routes accept traffic
the moment the first model resolves.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.agents.kernels.model_catalog import CATALOG
from app.agents.kernels.model_client import (
    ResolvedModel,
    build_pydantic_ai_model,
)
from app.agents.kernels.provider_catalog import default_base_url_for, get_entry
from app.db.models.model_provider import ModelKey, ModelProvider
from app.db.models.vault import VaultItem
from app.db.session import get_session_factory
from app.services.vault import reveal_secret

log = logging.getLogger(__name__)

# Hard ceiling. A worker that can't warm 32 providers in 15s usually
# has a flaky network or a broken vault — we'd rather come up degraded
# than freeze every HTTP route waiting on dead upstreams.
_WARMUP_TOTAL_BUDGET_S = 15.0
_WARMUP_PER_TASK_TIMEOUT_S = 6.0
_WARMUP_PARALLELISM = 8


@dataclass(slots=True)
class _WarmTarget:
    workspace_id: uuid.UUID
    provider_kind: str
    model_name: str
    base_url: str | None
    api_key: str


async def _collect_targets() -> list[_WarmTarget]:
    """Scan enabled providers across all workspaces and unwrap their keys.

    One target per (provider, default model) — we deduplicate downstream
    via the model-build cache key (kind + model + base + key_hash).
    """
    factory = get_session_factory()
    async with factory() as session:
        stmt = (
            select(ModelProvider, ModelKey, VaultItem)
            .join(ModelKey, ModelKey.provider_id == ModelProvider.id)
            .join(VaultItem, VaultItem.id == ModelKey.vault_item_id)
            .where(
                ModelProvider.enabled.is_(True),
                ModelProvider.deleted_at.is_(None),
                ModelKey.enabled.is_(True),
            )
            .order_by(
                ModelProvider.workspace_id.asc(),
                ModelProvider.sort_order.asc(),
                ModelProvider.created_at.asc(),
            )
            .limit(64)
        )
        rows = (await session.execute(stmt)).all()

        out: list[_WarmTarget] = []
        for provider, _key, vault_item in rows:
            kind = provider.kind.value if hasattr(provider.kind, "value") else str(provider.kind)
            model_name = provider.default_model or _first_chat_model(kind)
            if not model_name:
                continue
            try:
                api_key = await reveal_secret(vault_item)
            except Exception as exc:  # pragma: no cover - vault hiccup
                log.debug(
                    "warmup vault reveal skipped provider=%s kind=%s err=%s",
                    provider.id,
                    kind,
                    exc,
                )
                continue
            out.append(
                _WarmTarget(
                    workspace_id=provider.workspace_id,
                    provider_kind=kind,
                    model_name=model_name,
                    base_url=provider.base_url or default_base_url_for(kind),
                    api_key=api_key,
                )
            )
        return out


def _first_chat_model(kind: str) -> str | None:
    rows = CATALOG.get(kind, [])
    if not rows:
        entry = get_entry(kind)
        if entry and entry.pydantic_ai_kind:
            rows = CATALOG.get(entry.pydantic_ai_kind, [])
    chat_rows = [row for row in rows if row.category != "embedding"]
    if not chat_rows:
        return None
    for row in chat_rows:
        if row.recommended:
            return row.model
    return chat_rows[0].model


def _warm_one(target: _WarmTarget) -> bool:
    """Synchronous; called in a thread so import + provider construction
    can't block the event loop. Returns True on success.
    """
    resolved = ResolvedModel(
        provider_kind=target.provider_kind,
        model_name=target.model_name,
        api_key=target.api_key,
        base_url=target.base_url,
        source="warmup",
    )
    built = build_pydantic_ai_model(resolved)
    return built is not None


async def warm_model_clients() -> None:
    """Best-effort warm-up. Safe to call from inside the FastAPI lifespan.

    Failures are logged at ``debug``; the budget is enforced via
    :func:`asyncio.wait_for` so a hung handshake never blocks boot.
    """
    started = time.monotonic()
    try:
        targets = await _collect_targets()
    except Exception:  # pragma: no cover - DB hiccup at boot
        log.exception("model warmup: target collection failed; skipping")
        return

    if not targets:
        log.info("model warmup: no enabled providers, nothing to warm")
        return

    sem = asyncio.Semaphore(_WARMUP_PARALLELISM)
    successes = 0
    failures = 0

    async def _run(t: _WarmTarget) -> None:
        nonlocal successes, failures
        async with sem:
            try:
                ok = await asyncio.wait_for(
                    asyncio.to_thread(_warm_one, t),
                    timeout=_WARMUP_PER_TASK_TIMEOUT_S,
                )
            except TimeoutError:
                failures += 1
                log.debug(
                    "model warmup timeout provider=%s model=%s",
                    t.provider_kind,
                    t.model_name,
                )
                return
            except Exception as exc:  # pragma: no cover - provider import / construction
                failures += 1
                log.debug(
                    "model warmup failed provider=%s model=%s err=%s",
                    t.provider_kind,
                    t.model_name,
                    exc,
                )
                return
            if ok:
                successes += 1
            else:
                failures += 1

    try:
        await asyncio.wait_for(
            asyncio.gather(*(_run(t) for t in targets)),
            timeout=_WARMUP_TOTAL_BUDGET_S,
        )
    except TimeoutError:
        log.info(
            "model warmup: budget %ss exceeded; continuing with %d/%d warm",
            _WARMUP_TOTAL_BUDGET_S,
            successes,
            len(targets),
        )

    elapsed = time.monotonic() - started
    log.info(
        "model warmup done: %d ok, %d fail, %d targets in %.2fs",
        successes,
        failures,
        len(targets),
        elapsed,
    )
