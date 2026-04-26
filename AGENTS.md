# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository.
The more specific [`backend/AGENTS.md`](backend/AGENTS.md) and
[`frontend/AGENTS.md`](frontend/AGENTS.md) override this root file when
you're editing inside those folders.

For generic LLM-coding behavioral guidelines (think before coding,
simplicity first, surgical changes, goal-driven execution) see
[`CLAUDE.md`](CLAUDE.md). For the original Chinese-language project
rules see [`.claude/CLAUDE.md`](.claude/CLAUDE.md) — this file is the
English consolidation.

## What this repo is

**SenHarness** is an open-source, multi-tenant Harness Engineering
runtime for enterprise AI agents. Same binary serves a single company
on-prem (`docker compose up`) or thousands of tenants in SaaS.

Six layers map every feature to a Harness layer (see
[`docs/architecture.md`](docs/architecture.md)):

```
L6  Constraints & Recovery     can / can't · validate · retry · rollback
L5  Evaluation & Observability accept · trace · blame
L4  Memory & State             task · result · long-term · prefs
L3  Execution Orchestration    plan · exec · verify
L2  Tool System                which · when · how results flow
L1  Context Governance         role · goals · slice
```

## Tech stack at a glance

| Layer    | Stack                                                                                     |
|----------|-------------------------------------------------------------------------------------------|
| Backend  | Python 3.12 · FastAPI · SQLAlchemy 2 async · Pydantic 2 · pydantic-ai ecosystem · Alembic |
| Frontend | Next.js 15 (App Router) · React 19 · TypeScript 5.7 · shadcn/ui · Tailwind v4 · next-intl |
| Data     | PostgreSQL + pgvector · Redis                                                              |
| Runtime  | Docker Compose (dev / prod) · APScheduler · arq                                            |

## Repository layout

```
SenHarness/
├─ backend/        FastAPI + SQLAlchemy + pydantic-ai      → backend/AGENTS.md
├─ frontend/       Next.js 15 · shadcn/ui · Tailwind 4     → frontend/AGENTS.md
├─ docs/           architecture · adapters · whitepapers
├─ docker-compose*.yml   dev / prod / frontend-only
├─ Makefile        top-level dev workflow
└─ .env.example    canonical env vars (copy to .env)
```

## Common dev commands

`docker-compose.yml` is the **dev-mode default** — plain `docker compose
up -d` runs the full stack with source bind mounts, `uvicorn --reload`,
and `pnpm dev`. Code changes under `backend/app/**` and
`frontend/src/**` take effect without rebuilding. `docker-compose.prod.yml`
is a **standalone** production stack (Traefik + TLS + hardened networking)
passed alone via `-f`. The `make` targets wrap both.

```bash
make up              # start full stack (db · redis · backend · frontend)
make migrate         # alembic upgrade head (inside backend container)
make seed            # seed default workspace + agent
make create-admin    # create a platform admin identity
make lint            # ruff (backend) + next-lint (frontend)
make typecheck       # ty (backend) + tsc --noEmit (frontend)
make test            # pytest (backend) + vitest (frontend)
make logs            # tail combined logs
make sh-backend      # bash inside backend container
make nuke            # DANGER: down -v, wipes DB volumes
```

For host-side iteration (no Docker) use `pnpm dev` in `frontend/` and
`python -m cli.commands server run` in `backend/`. See the per-package
AGENTS.md for the full host workflow.

## Cross-cutting rules

These apply to **every** change, regardless of stack:

1. **Naming**. The technical entity is always called `Agent` — table
   `agents`, route `/api/v1/agents`, type `Agent`. The user-visible
   label is rendered dynamically from the workspace setting
   `branding.agent_term`, which is a slug that the frontend maps to
   the active locale via [`messages/<locale>.json → agentTerm`](frontend/messages):
   `agent` (default → "智能体" / "Agent"), `default` (→ "助理" /
   "Assistant"), `digital_employee`, `partner`, `secretary`. Never
   hardcode the surface label; always render through
   [`AgentTermLabel`](frontend/src/components/nav/AgentTermLabel.tsx).
2. **Multi-tenant scoping**. Every domain table carries a
   `workspace_id` foreign key. When you add a model, queries must
   filter by the caller's workspace; cross-workspace reads require an
   explicit platform-admin path.
3. **Secrets** flow only through the keyring abstraction
   ([`backend/app/security/keyring/`](backend/app/security/keyring))
   plus envelope encryption
   ([`backend/app/security/crypto.py`](backend/app/security/crypto.py)).
   Never write a plaintext secret to the DB, a config file, or a log.
4. **Conventional Commits** —
   `feat: / fix: / chore: / docs: / refactor: / test:`. Pre-commit
   hooks live in [`backend/.pre-commit-config.yaml`](backend/.pre-commit-config.yaml);
   run `pre-commit install` once per clone.
5. **i18n**. All user-facing strings on the frontend go through
   `frontend/messages/<locale>.json`. Backend error codes are stable
   keys; the frontend maps them to localized copy.

## Hard don'ts

1. Don't read or write plaintext secrets to disk or DB.
2. Don't call `pydantic_ai.Agent` directly inside an agent run —
   always go through the `AgentBackend` protocol
   ([`backend/app/agents/kernels/base.py`](backend/app/agents/kernels/base.py)).
3. Don't put business logic in route handlers — push it down to
   `app/services/`.
4. Don't `fetch()` directly from a frontend component — go through
   [`frontend/src/lib/api.ts`](frontend/src/lib/api.ts).
5. Don't decode JWTs by hand — use the helpers in
   [`backend/app/core/security.py`](backend/app/core/security.py).
6. CRUD goes through
   [`backend/app/db/repository.py`](backend/app/db/repository.py)
   (`AsyncRepository[TModel, TRead]`) plus a thin Service. Don't
   reach for an external CRUD generator.

## Where to read next

- [`docs/quickstart.md`](docs/quickstart.md) — 10-minute company setup
- [`docs/architecture.md`](docs/architecture.md) — L1–L6 deep dive
- [`docs/harness-engineering.md`](docs/harness-engineering.md) — paradigm whitepaper
- [`docs/adapters.md`](docs/adapters.md) — write a custom Agent Runtime
- [`docs/channels.md`](docs/channels.md) — write an IM channel provider
- [`docs/reliability.md`](docs/reliability.md) — L6 runtime recovery guards
- [`docs/evaluation.md`](docs/evaluation.md) — independent QA + trace replay
- [`docs/four-layer-memory.md`](docs/four-layer-memory.md) — L4 memory model
- [`docs/deployment.md`](docs/deployment.md) — production hardening
- [`CLAUDE.md`](CLAUDE.md) — generic LLM behavioral guardrails
- [`.claude/CLAUDE.md`](.claude/CLAUDE.md) — original Chinese rules
