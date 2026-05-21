# AGENTS.md

Operations manual for AI coding agents (Codex, Cursor, Copilot, Jules,
Claude Code, ...). Targets agents, not humans. Nested
`backend/AGENTS.md` and `frontend/AGENTS.md` override this file when
you edit inside those folders. Claude Code reads `CLAUDE.md`, which
forwards here.

## Behavior

- **Think first.** State assumptions; if uncertain, ask. Don't pick a
  branch silently when multiple interpretations exist.
- **Simplicity first.** Write the minimum code that solves the asked
  problem. No speculative abstractions, no unrequested flexibility.
- **Surgical changes.** Touch only what the request requires. Don't
  reformat or refactor adjacent code. Remove only the orphans your own
  changes created.
- **Goal-driven.** Convert each task into verifiable success criteria
  (failing test, command exit code, log assertion) and loop until met.

## Stack

| Layer    | Stack                                                                                |
|----------|--------------------------------------------------------------------------------------|
| Backend  | Python 3.12 · FastAPI · SQLAlchemy 2 async · Pydantic 2 · pydantic-ai 1.84 · Alembic |
| Frontend | Next.js 15 · React 19 · TypeScript 5.7 · shadcn/ui · Tailwind 4 · next-intl · pnpm 9 |
| Data     | PostgreSQL + pgvector · Redis                                                        |
| Runtime  | Docker Compose · APScheduler · arq                                                   |

## Layout

```
SenHarness/
├─ backend/      FastAPI · SQLAlchemy · pydantic-ai     → backend/AGENTS.md
├─ frontend/     Next.js · shadcn/ui · Tailwind         → frontend/AGENTS.md
├─ docker-compose*.yml · Makefile · .env.example
```

## Commands

`docker-compose.yml` is the dev default (source mounts, `uvicorn --reload`,
`pnpm dev`). `docker-compose.prod.yml` is the standalone production stack.

```bash
make up              # full dev stack (db · redis · backend · frontend)
make migrate         # alembic upgrade head
make seed            # default workspace + agent
make create-admin    # platform admin
make lint            # ruff + eslint
make typecheck       # ty + tsc --noEmit
make test            # pytest + vitest
make sh-backend / make sh-frontend
```

## Cross-cutting rules

1. **Agent label.** Technical entity is always `Agent` (table/route/type).
   User-visible label is dynamic via `branding.agent_term` rendered
   through `AgentTermLabel`. Never hardcode "Agent" / "Assistant" in JSX, i18n defaults, or backend strings.
2. **Multi-tenant scoping.** Every domain table carries `workspace_id`.
   Every query must filter by the caller's workspace; cross-workspace
   reads require an explicit platform-admin path.
3. **Secrets.** Flow only through the keyring abstraction
   (`backend/app/security/keyring/`) plus envelope encryption
   (`backend/app/security/crypto.py`). Never write plaintext secrets to
   the DB, a config file, or a log.
4. **Conventional Commits.** `feat: / fix: / chore: / docs: / refactor: / test:`.
   Run `pre-commit install` once per clone.
5. **i18n.** All frontend user-facing strings live in
   `frontend/messages/<locale>.json`. Backend error codes are stable
   keys; the frontend maps them to localized copy.

## Docs hygiene

`docs/` is a fixed skeleton — README + `architecture` ·
`harness-engineering` · `deployment` · `quickstart` · `commands` ·
`patterns` · `testing` · `adding-features` · `skills` ·
`runtime-and-jobs` · `extensions-and-governance`. New work edits an
H2 section inside one of these files.

- Do NOT add new top-level `docs/*.md` files. If a new topic genuinely
  doesn't fit any thematic file, propose splitting the closest existing
  file first; new top-level docs require explicit human review.
- Do NOT create `docs/changelog/` or any per-milestone summary
  markdown. A milestone is a git commit, not a markdown file. Suggested
  commit messages go in the PR description, never in `docs/`.
- Code references like `docs/<feature>.md` (in comments, docstrings,
  `docs_url=`, `metadata` fields) must point at one of the 11 thematic
  files plus an anchor — `docs/extensions-and-governance.md#channel-providers`,
  not `docs/channels.md`.

## Observability

- **Logs go to stdout only.** Backend and frontend processes write
  structured logs to stdout/stderr. `LOG_FORMAT=text` for dev,
  `LOG_FORMAT=json` for prod (Loki / Datadog / CloudWatch ingest from
  the container stream).
- **No log files in the repo.** Never `open(..., ".log")` from
  business code. If a process needs file logs in dev, set `LOG_DIR`
  and the shared logger writes one rotating file under
  `${STORAGE_LOCAL_PATH}/logs/`. There is no second log path.
- **Log message strings in English.** They are queried, alerted on,
  and shipped to third-party tools. User-facing copy still goes
  through `frontend/messages/<locale>.json`; backend exposes stable
  error *codes* the frontend localizes.
- **Disk layout under `STORAGE_LOCAL_PATH`** (container
  `/data/storage`, dev `./.data/storage`):
  - `attachments/<workspace_id>/<yyyymm>/`  user uploads
  - `scratch/<session_id>/`                 per-run tool outputs
  - `skills/<workspace_id>/<slug>/`         authored skills
  - `plugins/<name>/`                       installed plugins
  - `sandbox/<session_id>/`                 sandbox FS
  - `workspace_repos/<workspace_id>/`       coding workspaces
  - `logs/`                                 only when `LOG_DIR` is set

## Hard don'ts

1. Never write or log plaintext secrets.
2. Never call `pydantic_ai.Agent` directly inside an agent run — go
   through the `AgentBackend` protocol.
3. Never put business logic in route handlers — push it down to
   `backend/app/services/`.
4. Never call `fetch()` directly from a frontend component — go through
   `frontend/src/lib/api.ts`.
5. Never decode JWTs by hand — use `backend/app/core/security.py`.
6. CRUD goes through `AsyncRepository` in `backend/app/db/repository.py`,
   not an external generator.
7. Never commit ad-hoc debug modules (e.g. `_debug_<id>.py`) or
   hardcoded log paths. Diagnostics use `logging.getLogger(__name__)`
   and only land on disk via `LOG_DIR`.
8. Never write disk artifacts outside `STORAGE_LOCAL_PATH`. Absolute
   paths in business code are forbidden.
