#!/bin/sh
# ═════════════════════════════════════════════════════════════════
# SenHarness · container entrypoint (prod)
#
# Runs inside the `backend` container before the main CMD starts. The
# responsibilities are, in order:
#
#   1. Wait for PostgreSQL to accept connections (bounded by PG_WAIT_S).
#   2. Apply Alembic migrations — so a fresh deploy auto-upgrades the
#      schema without manual `docker compose exec backend migrate`.
#   3. Optional first-run seed if SENHARNESS_AUTO_SEED=true — useful in
#      CI / staging, opt-in for prod because seeding a running system is
#      a foot-gun.
#   4. Exec the command supplied via `CMD` (uvicorn / arq / scheduler).
#
# The worker and scheduler containers also invoke this (via the compose
# service override) so migrations are applied exactly once per deploy
# regardless of which container happens to win the race.
# ═════════════════════════════════════════════════════════════════

set -eu

log() {
    echo "[entrypoint] $*"
}

PG_WAIT_S="${PG_WAIT_S:-60}"
DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-senharness}"
DB_NAME="${DB_NAME:-senharness}"


wait_for_db() {
    log "waiting up to ${PG_WAIT_S}s for Postgres at ${DB_HOST}:${DB_PORT}"
    local i=0
    while [ "$i" -lt "$PG_WAIT_S" ]; do
        if python -c "
import os, sys
import asyncio
import asyncpg

async def _check():
    try:
        conn = await asyncpg.connect(
            host='${DB_HOST}',
            port=int('${DB_PORT}'),
            user='${DB_USER}',
            password=os.environ.get('DB_PASSWORD', ''),
            database='${DB_NAME}',
            timeout=2.0,
        )
        await conn.close()
    except Exception as e:
        sys.exit(1)

asyncio.run(_check())
" 2>/dev/null; then
            log "Postgres ready after ${i}s"
            return 0
        fi
        i=$((i + 1))
        sleep 1
    done
    log "ERROR: Postgres did not become ready within ${PG_WAIT_S}s"
    exit 1
}


apply_migrations() {
    # In a multi-service deploy (backend + worker + scheduler all share
    # this entrypoint) multiple processes may race here. Alembic is
    # idempotent — running `upgrade head` twice in parallel is safe
    # because each migration wraps in a transaction and takes a
    # pg_advisory_lock. Still, we log so operators can see who won.
    log "applying Alembic migrations (upgrade head)"
    alembic upgrade head
}


maybe_seed() {
    if [ "${SENHARNESS_AUTO_SEED:-false}" = "true" ]; then
        log "SENHARNESS_AUTO_SEED=true — seeding defaults"
        python -m cli.commands seed || log "seed failed (non-fatal)"
    fi
}


main() {
    log "SenHarness ${APP_ENV:-development} entrypoint starting"
    wait_for_db
    apply_migrations
    maybe_seed

    if [ "$#" -eq 0 ]; then
        log "ERROR: no command supplied after entrypoint"
        exit 2
    fi

    log "handing off to: $*"
    exec "$@"
}

main "$@"
