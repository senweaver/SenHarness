# Patterns

Code conventions, async pitfalls, and architectural invariants. If a
review comment cites a "convention", the explanation is here.

## Layered architecture (backend)

```
HTTP → app/api/v1/<module>.py    (thin: parse, authorize, delegate)
       ↓ DI from app/api/deps.py
       app/services/<module>.py  (business logic, owns transactions)
       ↓
       app/repositories/<entity>.py    (AsyncRepository[TModel, TRead])
       ↓
       app/db/models/<entity>.py       (SQLAlchemy ORM; every domain
                                        table carries workspace_id)
```

Strict invariants:

1. **Routes never contain direct database calls.** Routes parse + authorize
   + delegate. All data access goes through services, which delegate to
   repositories.
2. **Services own transactions.** Repositories call `db.flush(...)`;
   only services call `commit()`. A service represents one logical unit
   of work and commits at the end.
3. **Schemas describe wire payloads only** — never reuse ORM models in
   `app/schemas/`. Separate `Create`, `Update`, `Response` Pydantic
   models per resource.
4. **CRUD goes through `AsyncRepository`** in
   [`backend/app/db/repository.py`](../backend/app/db/repository.py),
   not through an external generator or hand-rolled raw SQL.
5. **Routes must register in `app/api/router.py`.** A route file that
   isn't imported there is not mounted.

## Multi-tenant scoping

Every domain table carries `workspace_id`. Every query must filter by
the caller's workspace; cross-workspace reads require an explicit
platform-admin path.

```python
# Good — service filters on workspace
async def list_for_workspace(
    self, db: AsyncSession, *, workspace_id: UUID,
) -> list[Foo]:
    stmt = select(Foo).where(Foo.workspace_id == workspace_id)
    return list((await db.execute(stmt)).scalars())

# Bad — route reads ORM directly, no workspace filter
@router.get("/foos")
async def list_foos(db = Depends(get_db)):
    return (await db.execute(select(Foo))).scalars().all()
```

Cross-tenant probes should return `404 <resource>.not_found` rather
than 403 — leaking row existence is itself an information leak.

## Async session pitfalls

`app/db/session.py` builds the session with `expire_on_commit=False`.
Two patterns still bite:

### 1. Server-computed columns after UPDATE

Models with `onupdate=now()` (e.g. `Timestamped` in `app/db/mixins.py`):
`await db.flush([row])` issues UPDATE without `RETURNING`, so the new
value is not in memory. The next `model_validate(row)` triggers a lazy
fetch outside the async greenlet and raises `MissingGreenlet`.

Either `await db.refresh(row)` after commit, **or** build the read
model from `db.execute(... .returning(*cols))` directly:

```python
# Good
stmt = (
    update(Foo)
    .where(Foo.id == foo_id)
    .values(name=new_name)
    .returning(Foo)
)
result = await db.execute(stmt)
await db.commit()
return FooRead.model_validate(result.scalar_one())

# Also good
foo.name = new_name
await db.flush()
await db.commit()
await db.refresh(foo)               # ← critical
return FooRead.model_validate(foo)
```

### 2. `AsyncSession.run_sync(fn)` hands `fn` a `Session`, not a `Connection`

`sqlalchemy.inspect(sync_session)` raises `NoInspectionAvailable`.
Inspect via `inspect(sync_session.connection()).has_table(...)` instead.
Same for `get_columns`, `has_index`, etc.

```python
# Good
async with engine.begin() as conn:
    has_table = await conn.run_sync(
        lambda sync_conn: inspect(sync_conn).has_table("foo")
    )

# Bad — sync_conn is a Session, not a Connection
async with AsyncSession(engine) as db:
    await db.run_sync(lambda s: inspect(s).has_table("foo"))   # raises
```

## Migrations

```bash
make migration m="feat: add foo column"
```

Review the diff before committing — autogen misses enums, server
defaults, index renames, and check constraints. **Never edit a migration
that has shipped to `main`** — add a forward fix.

Three things autogen always misses:

1. **Enum values added in code but not in the existing PG type** —
   manually `ALTER TYPE foo_enum ADD VALUE 'new_value'` in the upgrade.
2. **Server defaults / index renames** — the diff shows a drop+recreate,
   which is destructive. Use `op.alter_column` / `op.execute` instead.
3. **JSONB schema migrations** — autogen sees the column shape, not the
   payload shape. If you renamed a JSONB field, write an
   `op.execute` update for existing rows.

## Extension contracts

Three pluggable surfaces. Each follows the same pattern: one file, one
class, one `register_*` call. **No schema migrations** for adding a new
adapter / provider / connector.

* **Agent runtime.** Implement `AgentBackend`
  ([`app/agents/kernels/base.py`](../backend/app/agents/kernels/base.py))
  and register in `app/agents/kernels/registry.py`. Officially supported:
  `app/agents/kernels/native/` and `app/agents/kernels/openclaw/`. See
  [extensions-and-governance.md#agent-runtime-adapters](extensions-and-governance.md#agent-runtime-adapters).
* **Tools.** Drop a module under `app/agents/tools/` and add it to
  `BUILTIN_TOOL_REGISTRY` in `app/agents/tools/__init__.py`. Shared
  per-run state goes in `_context.py`.
* **Channels.** Provider modules under `app/services/channels/`.
  `config_json` secrets are envelope-encrypted by `_secret_box.py`;
  decrypt via `decrypt_config(...)` before passing to provider methods.
  Stream-mode providers override `supports_stream` / `stream_available` /
  `run_stream`. Runtime in `app/services/channel_runtime.py` owns
  reconnect; dispatch in `channel_dispatch.py`.

Harness L3–L6 helpers (planning, reliability guards, evaluator, shields,
sandbox) live under `app/agents/harness/`. **Keep recovery logic there
— don't sprinkle retry/timeout into routes.**

## Hard don'ts

1. **Never write or log plaintext secrets.** Secrets flow only through
   the keyring abstraction
   ([`backend/app/security/keyring/`](../backend/app/security/keyring/))
   plus envelope encryption
   ([`backend/app/security/crypto.py`](../backend/app/security/crypto.py)).
2. **Never call `pydantic_ai.Agent` directly inside an agent run** —
   go through the `AgentBackend` protocol.
3. **Never put business logic in route handlers** — push it down to
   `backend/app/services/`.
4. **Never call `fetch()` directly from a frontend component** — go
   through [`frontend/src/lib/api.ts`](../frontend/src/lib/api.ts).
5. **Never decode JWTs by hand** — use
   [`backend/app/core/security.py`](../backend/app/core/security.py).
6. **Never commit ad-hoc debug modules** (`_debug_<id>.py`) or
   hardcoded log paths. Diagnostics use `logging.getLogger(__name__)`
   and only land on disk via `LOG_DIR`.
7. **Never write disk artifacts outside `STORAGE_LOCAL_PATH`**.
   Absolute paths in business code are forbidden.

## Logging

Single entry point: `app.core.logging.setup_logging()` is called from
`app/main.py` lifespan and from CLI entrypoints. All modules use
`log = logging.getLogger(__name__)`.

Knobs:

* `LOG_FORMAT=text|json` — `text` for dev terminals, `json` for prod
  (root handler stays a `StreamHandler` either way).
* `LOG_DIR` (default empty) — when set, `setup_logging()` attaches a
  `RotatingFileHandler` writing one file `${LOG_DIR}/backend.log`. Use
  `${STORAGE_LOCAL_PATH}/logs` so all on-disk state stays under the
  storage root. `LOG_FILE_MAX_BYTES` and `LOG_FILE_BACKUPS` control
  rotation.

**Log messages in English.** They are queried, alerted on, shipped to
third-party tools. User-facing copy still goes through
`frontend/messages/<locale>.json`; backend exposes stable error
*codes* the frontend localizes.

SDK policy: channel SDKs (`botpy`, `lark_oapi`, `dingtalk_stream`,
`discord`) ship their own `FileHandler`s that would otherwise drop
files like `botpy.log` in the process CWD. `setup_logging()` strips
those handlers and sets `propagate=True` so their records flow through
the one configured pipeline. **Do not call `basicConfig`, do not attach
extra `FileHandler`s elsewhere.**

## Naming + comments

* **Semantic full names, not abbreviations** — `provider_id`, not
  `pid`; `workspace_id`, not `wid`. No `data` / `result` / `temp` /
  `tmp` in public APIs.
* When wrapping a third-party hook or client, return its full output
  object — don't hand-pick fields. Downstream consumers can pick what
  they need.
* **No comments unless necessary** — default to no comment. Add only
  to explain non-obvious intent, constraints, or tradeoffs that the
  code itself cannot convey.
* **No descriptive comments** ("// Increment the counter"). Names must
  be self-explanatory.
* **No meta-comments** ("// renamed from foo"). Clean up immediately.
* **No library mentions in comments** — put deps in the project's
  manifest.
