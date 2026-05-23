"""Shared pytest fixtures.

Unit tests need no external resources and get the lightweight path
(environment defaults + cached settings reset).

Integration tests need a Postgres + Redis pair. When ``testcontainers``
is installed we spin up ephemeral containers per test *session*; when
it isn't we fall back to env-supplied connection strings.

CI policy: the GitHub Actions ``backend-test`` job runs
``pytest -m "not requires_db"`` against a stripped runner (no Postgres,
no Redis, no alembic step). Any test that transitively pulls in one
of the DB-bound fixtures listed in ``_DB_BOUND_FIXTURES`` is auto-tagged
``requires_db`` by ``pytest_collection_modifyitems`` below and therefore
deselected on CI. Running ``make test`` locally provisions Postgres
(via ``testcontainers`` or ``make up``) and runs the full suite.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

# Fixtures that ultimately require a live Postgres (and, for ``async_client``,
# also Redis). Any test pulling one of these in — directly or transitively —
# is marked ``requires_db`` at collection time and skipped on CI.
_DB_BOUND_FIXTURES = frozenset(
    {
        "_pg_container",
        "_migrated_engine",
        "db_session",
        "identity",
        "workspace",
        "agent",
        "async_client",
        "share_session_for_subagent_hooks",
    }
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    requires_db = pytest.mark.requires_db
    for item in items:
        fixturenames = getattr(item, "fixturenames", ())
        if any(name in _DB_BOUND_FIXTURES for name in fixturenames):
            item.add_marker(requires_db)


# ─── Test environment defaults ───────────────────────────────
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("APP_DEBUG", "true")
os.environ.setdefault("SENHARNESS_MASTER_KEY", "test-" + "0" * 20)
# Integration tests that need a DB set these before importing ``app``.
# If testcontainers is available we rewrite them in ``_pg_container``;
# otherwise the CI-provided env vars take precedence.


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Ensure each test sees fresh settings (monkeypatched env takes effect)."""
    from app.core import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


# ─── Testcontainers (optional) ───────────────────────────────
@pytest.fixture(scope="session")
def _pg_container() -> Iterator[str | None]:
    """Spin up an ephemeral Postgres with pgvector preinstalled.

    Returns a DSN usable by asyncpg, or None when testcontainers isn't
    importable (dev machines without Docker). Session-scoped so every
    test that needs a DB shares the same container — ~5s startup.
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        yield None
        return

    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        dsn = pg.get_connection_url().replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        os.environ["DB_HOST"] = pg.get_container_host_ip()
        os.environ["DB_PORT"] = str(pg.get_exposed_port(5432))
        os.environ["DB_USER"] = pg.username
        os.environ["DB_PASSWORD"] = pg.password
        os.environ["DB_NAME"] = pg.dbname
        yield dsn


@pytest.fixture(scope="session")
def _redis_url() -> str | None:
    """Resolve a Redis URL — testcontainers in dev, env in CI."""
    # If CI (or the operator's .env) set REDIS_HOST, just honour it.
    if os.environ.get("REDIS_HOST"):
        host = os.environ["REDIS_HOST"]
        port = os.environ.get("REDIS_PORT", "6379")
        return f"redis://{host}:{port}/0"

    try:
        from testcontainers.redis import RedisContainer
    except ImportError:
        return None

    # ``RedisContainer`` is a session-scoped context manager we enter
    # explicitly. pytest-asyncio doesn't play nice with session fixtures
    # that return context managers, so we hand-roll.
    container = RedisContainer("redis:7-alpine")
    container.start()
    host = container.get_container_host_ip()
    port = container.get_exposed_port(6379)
    os.environ["REDIS_HOST"] = host
    os.environ["REDIS_PORT"] = str(port)
    # Redis in testcontainers has no password by default; that matches
    # the dev-mode REDIS_PASSWORD="".
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def db_available(_pg_container) -> bool:
    """True when DB integration tests can run.

    Prefers a running testcontainer, falls back to env-supplied
    coordinates (CI sets these via GitHub-Actions ``services``).
    """
    if _pg_container:
        return True
    return bool(os.environ.get("DB_HOST"))


@pytest.fixture(scope="session")
def redis_available(_redis_url) -> bool:
    return _redis_url is not None


# ─── Application engine + session ────────────────────────────
@pytest.fixture(scope="session")
def _migrated_engine(_pg_container, db_available):
    """Apply alembic migrations once per session to the test DB.

    Sync fixture — alembic ``env.py`` already wraps the upgrade in
    ``asyncio.run()``, which needs a fresh loop. A sync fixture runs
    outside any pytest-asyncio loop, so ``asyncio.run`` is free to own
    one. ``get_engine()`` is lazy: no asyncpg pool is created here.
    """
    if not db_available:
        pytest.skip("Postgres not available — install testcontainers or set DB_HOST")

    from alembic import command
    from alembic.config import Config

    from app.db.session import get_engine

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")
    return get_engine()


@pytest_asyncio.fixture
async def db_session(_migrated_engine) -> AsyncIterator:
    """One SQLAlchemy AsyncSession per test, rolled back on exit.

    Tests that mutate (create workspace, insert agents) don't leak
    between each other — everything happens inside a transaction that
    never commits.

    The asyncpg pool is disposed at fixture entry so it rebinds to the
    current test's event loop. pytest-asyncio defaults to a fresh
    function-scoped loop per test, while ``get_engine()`` is an
    ``lru_cache`` singleton — without dispose, the second test inherits
    a pool tied to the first test's closed loop and every query raises
    ``RuntimeError: Future attached to a different loop``.
    """
    from app.db.session import get_engine, get_session_factory

    await get_engine().dispose()

    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest.fixture
def share_session_for_subagent_hooks(monkeypatch, db_session) -> None:
    """Route subagent lifecycle ``get_session_factory()`` opens through ``db_session``.

    Lifecycle hooks call ``commit()``; remap to ``flush()`` so rows stay inside
    the per-test transaction (fixture rollback) and the asyncpg pool is not left
    bound to a closed loop. A lock serializes concurrent hook access — shared
    ``AsyncSession`` is not safe under parallel ``delegate_batch`` children.
    """
    lock = asyncio.Lock()

    async def _flush_not_commit() -> None:
        await db_session.flush()

    monkeypatch.setattr(db_session, "commit", _flush_not_commit)

    @asynccontextmanager
    async def _ctx():
        async with lock:
            yield db_session

    monkeypatch.setattr("app.db.session.get_session_factory", lambda: _ctx)


# ─── HTTP client ─────────────────────────────────────────────
@pytest_asyncio.fixture
async def async_client(_migrated_engine, redis_available) -> AsyncIterator:
    """FastAPI HTTPX AsyncClient with lifespan started (so middleware
    and DB engine initialise). Useful for integration tests that hit
    real routes end-to-end.
    """
    if not redis_available:
        pytest.skip("Redis not available — install testcontainers or set REDIS_HOST")

    import httpx
    from httpx import ASGITransport

    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# ─── Domain object factories ─────────────────────────────────
@pytest_asyncio.fixture
async def identity(db_session):
    """A freshly-registered identity (email + password hashed).

    Skips the auto-provisioned personal workspace so the older fixture
    contract (just an Identity, no membership) still holds — the
    ``workspace`` fixture creates one explicitly when needed.
    """
    from app.services import auth as svc

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    result = await svc.register(
        db_session,
        email=email,
        name="Test User",
        password="correct horse battery staple",
        create_personal_workspace=False,
    )
    await db_session.flush()
    return result.identity


@pytest_asyncio.fixture
async def workspace(db_session, identity):
    """A workspace with the given identity as its owner."""
    from app.services import workspace as ws_svc

    ws = await ws_svc.create_workspace(
        db_session,
        name=f"Test {uuid.uuid4().hex[:6]}",
        slug=f"test-{uuid.uuid4().hex[:8]}",
        owner_identity_id=identity.id,
    )
    await db_session.flush()
    return ws


@pytest_asyncio.fixture
async def agent(db_session, workspace, identity):
    """A pydantic-ai backed agent inside ``workspace``."""
    from app.services import agent as svc

    a = await svc.create_agent(
        db_session,
        workspace_id=workspace.id,
        created_by=identity.id,
        name="Test Assistant",
        description="smoke test",
        persona_md="You are a test assistant.",
    )
    await db_session.flush()
    return a
