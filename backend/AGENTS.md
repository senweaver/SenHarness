# backend/AGENTS.md

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 async (asyncpg) · Pydantic 2 ·
pydantic-ai 1.84 ecosystem (`pydantic-ai-harness` · `-shields` ·
`-backend` · `-skills` · `subagents-pydantic-ai` · `pydantic-ai-todo`
· `pydantic-ai-middleware` · `summarization-pydantic-ai`) · Alembic ·
redis · arq · APScheduler · logfire · opentelemetry.

## Commands

```bash
make sh-backend                       # bash inside container
make lint-backend                     # ruff check .
make typecheck                        # ty check app
make test-backend                     # pytest -x
make migrate                          # alembic upgrade head
make migration m="describe change"    # alembic revision --autogenerate
```

Host iteration (requires Python 3.12 + `uv` or `pip`):

```bash
cd backend && uv venv && uv pip install -e ".[dev]"
ruff format . && ruff check . && ty check app && pytest -x
python -m cli.commands server run     # uvicorn entrypoint (single-file CLI)
python -m cli.commands seed | create-admin | scheduler run
```

`pyproject.toml` pins `asyncio_mode = "auto"`; no `@pytest.mark.asyncio`
on `async def test_*`.

## Layered architecture

```
HTTP → app/api/v1/<module>.py   (thin: parse, authorize, delegate)
       ↓ DI from app/api/deps.py
       app/services/<module>.py (business logic, owns transactions)
       ↓
       app/repositories/<entity>.py   (AsyncRepository[TModel, TRead])
       ↓
       app/db/models/<entity>.py      (SQLAlchemy ORM; every domain
                                       table carries workspace_id)
```

Routes must register in `app/api/router.py`. Services commit at the
end of one logical unit. Schemas in `app/schemas/` describe wire
payloads only — never reuse ORM models there.

## Extension contracts

- **Agent runtime.** Implement `AgentBackend` (`app/agents/kernels/base.py`)
  and register in `app/agents/kernels/registry.py`. Officially supported:
  `app/agents/kernels/native/` and `app/agents/kernels/openclaw/`.
  Never call `pydantic_ai.Agent` directly inside a run.
- **Tools.** Drop a module under `app/agents/tools/` and add it to
  `BUILTIN_TOOL_REGISTRY` in `app/agents/tools/__init__.py`. Shared
  per-run state goes in `_context.py`.
- **Channels.** Provider modules under `app/services/channels/`.
  `config_json` secrets are envelope-encrypted by `_secret_box.py`;
  decrypt via `decrypt_config(...)` before passing to provider methods.
  Stream-mode providers override `supports_stream` / `stream_available`
  / `run_stream`; the runtime in `app/services/channel_runtime.py`
  owns reconnect and dispatch into `channel_dispatch.py`.

Harness L3–L6 helpers (planning, reliability guards, evaluator,
shields, sandbox) live under `app/agents/harness/`. Keep recovery
logic there — don't sprinkle retry/timeout into routes.

## SQLAlchemy async session pitfalls

`app/db/session.py` builds the session with `expire_on_commit=False`.
Two patterns still bite:

1. **Server-computed columns after UPDATE.** Models with
   `onupdate=now()` (e.g. `Timestamped` in `app/db/mixins.py`):
   `await db.flush([row])` issues UPDATE without `RETURNING`, so the
   new value is not in memory. The next `model_validate(row)` triggers
   a lazy fetch outside the async greenlet and raises `MissingGreenlet`.
   Either `await db.refresh(row)` after commit, or build the read model
   from `db.execute(... .returning(*cols))` directly.

2. **`AsyncSession.run_sync(fn)` hands `fn` a `Session`, not a
   `Connection`.** `sqlalchemy.inspect(sync_session)` raises
   `NoInspectionAvailable`. Inspect via
   `inspect(sync_session.connection()).has_table(...)` instead. Same
   for `get_columns`, `has_index`, etc.

## Security

- JWT: only via `app/core/security.py`. Don't decode by hand or cache
  claims across requests.
- Vault: only via `app/services/vault.py` (keyring + envelope crypto).
- Sandbox: production refuses `kind=local execute=true` unless
  `SANDBOX_LOCAL_EXECUTE_PROD=true`. Default `kind=docker` or
  `kind=state` for new code.
- Outbound HTTP: call `app.core.url_safety.assert_safe_url` and
  re-validate every redirect hop.
- Rate limits: apply `app/core/rate_limit.py` to LLM-streaming and
  user-driven routes.

## Logging

Single entry point: `app.core.logging.setup_logging()` is called from
`app/main.py` lifespan and from CLI entrypoints. All modules use
`log = logging.getLogger(__name__)`.

Knobs:

- `LOG_FORMAT=text|json` — `text` for dev terminals, `json` for prod
  (root handler stays a `StreamHandler` either way).
- `LOG_DIR` (default empty) — when set, `setup_logging()` attaches a
  `RotatingFileHandler` to the root logger writing one file
  `${LOG_DIR}/backend.log`. Use `${STORAGE_LOCAL_PATH}/logs` so all
  on-disk state stays under the storage root. `LOG_FILE_MAX_BYTES` and
  `LOG_FILE_BACKUPS` control rotation.

SDK policy: channel SDKs (`botpy`, `lark_oapi`, `dingtalk_stream`,
`discord`) ship their own `FileHandler`s that would otherwise drop
files like `botpy.log` in the process CWD. `setup_logging()` strips
those handlers and sets `propagate=True` so their records flow through
the one configured pipeline. Do not call `basicConfig`, do not attach
extra `FileHandler`s elsewhere.

See [`app/core/logging.py`](app/core/logging.py).

## Tests

Layout: `tests/unit/<layer>/` (no DB / no HTTP — run by default) and
`tests/integration/` (DB + httpx AsyncClient). Strict markers
(`addopts = "-ra --strict-markers"`) — declare new markers in
`pyproject.toml`.

## Migrations

`alembic revision --autogenerate -m "feat: ..."`. Review the diff
(autogen misses enums, server defaults, index renames). Never edit a
migration that has shipped to `main` — add a forward fix.

## New endpoint checklist

1. Schema in `app/schemas/<module>.py`.
2. Repository method on the right `AsyncRepository` subclass.
3. Service in `app/services/<module>.py` — owns transaction, enforces
   workspace scoping + permissions.
4. Route in `app/api/v1/<module>.py`, registered in `app/api/router.py`.
5. Extend `app/services/permissions.py` if a new action exists.
6. Tests: unit for the service, integration for the route happy path
   plus at least one auth-failure case.
7. Migration if you touched ORM models.

## Doc updates

See the top-level [`AGENTS.md`](../AGENTS.md) "Docs hygiene" section:
edit an H2 inside one of the 11 thematic files in `docs/`; never create
new top-level `docs/*.md` or any `docs/changelog/*`.
