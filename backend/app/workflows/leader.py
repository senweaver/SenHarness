"""Redis-backed leader election for singleton background workers.

Use case: we run the APScheduler in its own container. If an operator
accidentally scales the service to replicas>1 (or does a rolling
deploy that briefly overlaps old+new), every instance would register
the same cron job and fire it N times. This module wraps the work
loop so only one instance holds the lease at a time — extras sit in a
hot-standby state polling for takeover.

Algorithm:
    * SET lease_key=<worker_id> NX EX=<lease_s>      — attempt
    * while held:
          - do work
          - SET lease_key=<worker_id> XX EX=<lease_s>  — renew
      (XX means "only if exists" — won't resurrect the lease after
       some other pod stole it.)
    * on shutdown:
          - DEL lease_key only if its value matches our worker_id
            (Lua CAS; otherwise we'd delete some other leader's lease
             and cause a second takeover)

Trade-offs deliberately accepted for V1:
    * Redis goes down ⇒ no leader ⇒ no cron fires. Better than dual-fire.
      Operators see it via readyz + the WARN log emitted every refresh.
    * Lease length 30s — on a clean shutdown the standby takes over
      within one refresh window. On a hard crash, worst-case cron delay
      is 30s. Tune via LEASE_TTL_S if cron misfire tolerance is tighter.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis

log = logging.getLogger(__name__)


LEASE_TTL_S = 30          # lease length
RENEW_INTERVAL_S = 10     # refresh every 1/3 TTL — 2 missed renewals tolerate
ACQUIRE_POLL_S = 5        # standby polling cadence when not leader


# Lua: atomically delete the lease only if we're still the holder.
_SAFE_DEL_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


def _worker_id() -> str:
    """Stable-ish identifier for this process. Container hostname +
    PID + random suffix is enough to avoid collision across restarts.
    """
    host = socket.gethostname()
    pid = os.getpid()
    return f"{host}-{pid}-{uuid.uuid4().hex[:6]}"


async def run_as_leader(
    redis_client: aioredis.Redis,
    *,
    lease_key: str,
    on_elected: Callable[[], Awaitable[None]],
    lease_ttl_s: int = LEASE_TTL_S,
    renew_interval_s: int = RENEW_INTERVAL_S,
    acquire_poll_s: int = ACQUIRE_POLL_S,
) -> None:
    """Run ``on_elected`` exactly once at a time across all callers.

    The ``on_elected`` coroutine is spawned as a background task when
    this instance wins the lease and cancelled when the lease is lost
    (either because Redis went away, or we're shutting down). It's the
    caller's responsibility to make ``on_elected`` cancellation-safe —
    typically by structuring it as a loop that awaits on shutdown /
    cancellation.

    Blocks forever (until CancelledError). Fits into an ``asyncio.run``
    inside a dedicated container. See ``cli.commands scheduler run``.
    """
    worker = _worker_id()
    log.info(
        "leader candidate %s starting (key=%s, ttl=%ds)", worker, lease_key, lease_ttl_s
    )

    election_task: asyncio.Task[None] | None = None
    try:
        while True:
            acquired = await redis_client.set(
                lease_key, worker, nx=True, ex=lease_ttl_s
            )
            if acquired:
                log.info("leader elected: %s", worker)
                election_task = asyncio.create_task(
                    _run_with_renewal(
                        redis_client,
                        worker=worker,
                        lease_key=lease_key,
                        on_elected=on_elected,
                        lease_ttl_s=lease_ttl_s,
                        renew_interval_s=renew_interval_s,
                    )
                )
                await election_task
                # ``_run_with_renewal`` returning cleanly means we lost
                # the lease (Redis restart, network partition, clock
                # skew). Fall through the loop to try to re-acquire.
                election_task = None
                log.warning("leader %s lost the lease, going back to standby", worker)
            else:
                await asyncio.sleep(acquire_poll_s)
    except asyncio.CancelledError:
        log.info("leader loop cancelled (shutdown) for worker %s", worker)
        if election_task is not None and not election_task.done():
            election_task.cancel()
            try:
                await election_task
            except asyncio.CancelledError:
                pass
        # Best-effort safe release — only deletes if we still hold it.
        try:
            await redis_client.eval(_SAFE_DEL_LUA, 1, lease_key, worker)
        except Exception:  # pragma: no cover - shutdown path, best effort
            log.debug("safe-release of lease %s failed (non-fatal)", lease_key)
        raise


async def _run_with_renewal(
    redis_client: aioredis.Redis,
    *,
    worker: str,
    lease_key: str,
    on_elected: Callable[[], Awaitable[None]],
    lease_ttl_s: int,
    renew_interval_s: int,
) -> None:
    """Spawn the leader work + renew the lease until we lose it."""
    work_task = asyncio.create_task(on_elected())
    try:
        while True:
            await asyncio.sleep(renew_interval_s)
            # XX = only renew if the key still exists AND holds our
            # worker id. If either fails, someone else has taken over.
            # Using SET with both XX and EX is equivalent to a CAS renew.
            ok = await redis_client.set(
                lease_key, worker, xx=True, ex=lease_ttl_s
            )
            if not ok:
                log.warning("lease %s renew failed — stepping down", lease_key)
                return
            if work_task.done():
                # ``on_elected`` finished / crashed — propagate.
                exc = work_task.exception()
                if exc is not None:
                    raise exc
                return
    finally:
        if not work_task.done():
            work_task.cancel()
            try:
                await work_task
            except asyncio.CancelledError:
                pass
            except Exception:  # pragma: no cover
                log.exception("leader work task raised during teardown")
