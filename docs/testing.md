# Testing

Backend uses pytest with `asyncio_mode = "auto"`. Frontend uses vitest.
Both layers ship unit + integration tiers; CI runs the full set on
every PR.

## Backend layout

```
backend/tests/
├─ unit/                # No DB, no HTTP — run by default
│   ├─ agents/
│   ├─ services/
│   └─ <layer>/
└─ integration/         # DB + httpx AsyncClient
    ├─ api/
    ├─ agents/
    └─ jobs/
```

Run:

```bash
make test-backend                            # full suite (-x stops on first failure)
make test-backend ARGS="-k goal"             # filter by keyword
cd backend && pytest tests/unit/agents       # one tier
pytest tests/integration/api/test_session_goals_api.py -x  # one file
```

`pyproject.toml` pins `asyncio_mode = "auto"`. **Never** decorate
`async def test_*` with `@pytest.mark.asyncio` — strict markers reject
unknown markers (`addopts = "-ra --strict-markers"`).

Declare new markers in `pyproject.toml` under
`[tool.pytest.ini_options].markers` before using them.

## Unit tier (`tests/unit/`)

No DB, no HTTP. Use module-level imports only — every file that imports
from `app.db.session` or `httpx.AsyncClient` belongs in `tests/integration/`.

Patterns:

* **Pure helpers** — call directly with synthetic inputs, assert on the
  return value. No fixtures needed beyond what pytest provides.
* **Services with DB dependencies** — pass a mock `AsyncSession` or use
  the `_fake_db` fixture in `tests/conftest.py`. Don't construct an
  actual DB engine.
* **Async generators** — use `aiter`/`anext` and an explicit list
  collector:

  ```python
  events = []
  async for ev in backend.run(req):
      events.append(ev)
  assert events[0].kind == RunEventKind.DELTA
  ```

## Integration tier (`tests/integration/`)

Full app + DB + Redis. Each test runs in a transaction that's rolled
back on teardown, so tests are order-independent without explicit
cleanup.

Fixtures (`tests/conftest.py`):

* `async_client` — `httpx.AsyncClient` bound to the test app.
* `db` — `AsyncSession` against the test DB; rolled back at end.
* `redis` — `fakeredis.aioredis` instance.
* `workspace` / `agent` / `identity` / `admin_identity` — pre-seeded
  rows ready to use.
* `auth_headers(identity)` — returns `{Authorization, X-Workspace-Id}`
  headers for the identity.

Typical shape:

```python
async def test_create_goal(async_client, workspace, identity):
    response = await async_client.post(
        f"/api/v1/sessions/{session.id}/goals",
        json={"text": "Ship M5", "threshold": 0.7},
        headers=auth_headers(identity),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "Ship M5"
```

For routes that depend on a worker side-effect (judge / curator /
evolver), the test should:

1. Trigger the route.
2. Assert the audit row landed via `audit_events` query.
3. **Optionally** invoke the ARQ task body directly with a fake `ctx`
   — `tests/integration/jobs/` has the patterns. Don't spin up a real
   worker process.

## Async session in tests

`tests/conftest.py` provides a session factory wired to the test DB.
The `db` fixture yields an `AsyncSession`; the surrounding wrapper
calls `db.rollback()` on teardown so every test starts clean.

When testing the M0 `expire_on_commit=False` pitfall (see
[patterns.md#async-session-pitfalls](patterns.md#async-session-pitfalls)),
the test should:

```python
foo = await foo_svc.create(db, ...)
await db.flush()
# Don't rely on server-default columns yet — refresh first:
await db.refresh(foo)
assert foo.created_at is not None
```

## Coverage targets

There is no enforced numeric coverage target. The rule is: **every
route gets at least one happy-path integration test + one auth-failure
case**. Every service method that owns a transaction gets a unit test
that exercises the success path + the most likely failure (constraint
violation, validation error, RBAC reject).

## What to test

| Layer        | Tier        | What                                                                                                  |
|--------------|-------------|-------------------------------------------------------------------------------------------------------|
| Pure helpers | unit        | Every branch + edge case. The whole point of separating helpers is they're cheap to test exhaustively.|
| Service      | unit        | Happy path + RBAC failure + constraint violation. Mock DB calls when feasible.                        |
| Repository   | unit        | Skip — repositories should be trivial. Coverage comes through service / integration tests.            |
| Route        | integration | Happy path + one auth-failure case + one RBAC-failure case. Body shape assertion.                     |
| ARQ task     | integration | Direct task invocation with fake `ctx`. Assert on persisted rows + audit rows.                        |
| Cron sweep   | integration | Direct task invocation. Assert per-workspace isolation + audit rows.                                  |

## Common patterns

### Cross-workspace 404

```python
async def test_cross_workspace_returns_404(async_client, identity_in_ws_a, ws_b):
    # ws_b has a row; identity from ws_a probes it
    response = await async_client.get(
        f"/api/v1/sessions/{ws_b_session.id}/goals",
        headers=auth_headers(identity_in_ws_a, workspace_id=ws_a.id),
    )
    assert response.status_code == 404
    assert response.json()["code"] == "session_goal.not_found"
```

The 404 (not 403) is the contract — leaking row existence to another
tenant is itself an information leak.

### ARQ task with breaker

```python
async def test_judge_breaker_open_returns_degraded(async_client, workspace, monkeypatch):
    monkeypatch.setattr("app.jobs._breaker.is_breaker_open", lambda *_: True)
    artifact = await create_artifact(db, workspace=workspace, ...)
    result = await judge_session_artifact({"redis": None}, artifact.id)
    assert result["status"] == "degraded"
    verdict = await get_verdict(db, artifact.id)
    assert verdict.degraded is True
    assert verdict.judged_by_model is None
```

### Notification cooldown

```python
async def test_cooldown_dedup(async_client, workspace, identity):
    # First emit lands.
    counters_a = await emit_event(db, event_key="goal.alignment_low", ...)
    assert counters_a.in_app_sent == 1
    # Second emit inside the window is dedup'd.
    counters_b = await emit_event(db, event_key="goal.alignment_low", ...)
    assert counters_b.cooldown_skipped == 1
    assert counters_b.in_app_sent == 0
```

## Frontend testing

```bash
cd frontend
pnpm test                            # vitest (tests/unit/)
pnpm test -- --filter=use-skills     # one test file
pnpm test:e2e                        # playwright (tests/e2e/)
```

Vitest specs live under `frontend/tests/unit/`, mirroring the `src/`
layout (`src/lib/foo.ts` → `tests/unit/lib/foo.test.ts`). Imports go
through the `@/` alias, never `../../../src/...`. Component tests use
React Testing Library; hook tests use `renderHook` from
`@testing-library/react`. When a hook needs the TanStack Query layer,
each test defines a thin `QueryClientProvider` wrapper inline.

Playwright specs live flat under `frontend/tests/e2e/` (one file per
feature, no milestone subfolders). Shared helpers (`helpers.ts` —
random ids, `requireStack`, identity bootstrap, UI smoke helpers;
`_bootstrap.ts` — personal-identity flow used by admin specs) sit
next to the specs.

Don't test backend behaviour from the frontend — write a backend
integration test (`backend/tests/integration/api/`) instead. Every
spec in `tests/e2e/` must drive the browser (`page.*`, `seedSession`,
or `gotoAndExpectH1`); REST-only contract checks belong in pytest.
