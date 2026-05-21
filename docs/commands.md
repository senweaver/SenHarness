# Commands

Dev + ops command reference. Top-level `Makefile` is the canonical entry
point for almost everything; the CLI helpers are for one-off tasks the
make targets don't cover.

## Stack management

```bash
make up               # full dev stack (db · redis · backend · frontend · worker · scheduler)
make down             # stop everything, keep volumes
make ps               # current service status
make logs             # tail logs for all services
make logs-backend     # tail one service
```

`docker-compose.yml` is the dev default (source mounts, `uvicorn
--reload`, `pnpm dev`). `docker-compose.prod.yml` layers prod overrides
(network isolation, resource limits, Traefik, healthchecks targeting
`/readyz`).

## Database

```bash
make migrate                          # alembic upgrade head
make migration m="describe change"    # alembic revision --autogenerate
make seed                              # default workspace + agent + demo data
make create-admin                      # platform admin (interactive)
make sh-db                             # psql shell
```

`alembic` advisory-locks during upgrade so concurrent boots from
multiple backend replicas are safe. **Never edit a migration that has
shipped to `main`** — add a forward fix.

## Backend dev loop

```bash
make sh-backend          # bash inside the backend container
make lint-backend        # ruff check .
make typecheck            # ty check app
make test-backend        # pytest -x
```

Host iteration (requires Python 3.12 + `uv` or `pip`):

```bash
cd backend
uv venv && uv pip install -e ".[dev]"
ruff format . && ruff check . && ty check app && pytest -x
python -m cli.commands server run     # uvicorn entrypoint (single-file CLI)
python -m cli.commands seed
python -m cli.commands create-admin
python -m cli.commands scheduler run
python -m cli.commands channels run    # standalone channel-runtime worker
```

`pyproject.toml` pins `asyncio_mode = "auto"`; **never** decorate `async
def test_*` with `@pytest.mark.asyncio`.

## Frontend dev loop

```bash
make sh-frontend          # bash inside the frontend container
make lint-frontend        # eslint + prettier
make test-frontend        # vitest
make build-frontend       # next build (production bundle)
```

Host iteration:

```bash
cd frontend
pnpm install
pnpm dev                  # next dev on :3000
pnpm test
pnpm lint
```

## Aggregate quality gates

```bash
make lint                 # ruff + eslint
make typecheck             # ty + tsc --noEmit
make test                  # pytest + vitest
```

Run all three before opening a PR. CI runs the same.

## Optional extras

Backend optional dependencies (installed via `pip install ".[<name>]"`):

| Extra              | What it enables                                                            |
|--------------------|----------------------------------------------------------------------------|
| `dev`              | Test deps (pytest, httpx test client, fakeredis, …)                        |
| `mcp`              | MCP protocol SDK — required by any workspace registering MCP servers.      |
| `channels-stream`  | Stream-mode SDKs (`lark-oapi`, `dingtalk-stream`, `wecom-aibot-sdk-python`, `discord.py`, `qq-botpy`). |
| `ssh-sandbox`      | `asyncssh` for the SSH execution backend.                                  |
| `plugin-signing`   | `pynacl` for ed25519 plugin signature verification.                        |

Each provider's `stream_available()` / `mcp.sdk_unavailable` / `sandbox.ssh_kind_disabled`
audits flag the missing extra cleanly so an operator knows what to
install.

## Cron + scheduler

The standalone scheduler runs every recurring job:

```bash
# Inside scheduler container (started by `make up`)
python -m cli.commands scheduler run
```

Cron slot map in [runtime-and-jobs.md](runtime-and-jobs.md#cron-slot-map).

## CLI subcommands

```bash
python -m cli.commands --help
python -m cli.commands rag-ingest /path/to/file.pdf --collection docs
python -m cli.commands rag-search "query" --collection docs
python -m cli.commands rag-sources                  # list sync sources
python -m cli.commands rag-source-add               # interactive add
python -m cli.commands rag-source-sync              # one-shot sync
```

Commands are auto-discovered from `backend/cli/commands/`. Add a new
subcommand: drop a file with a `register(app)` function exposing a
typer command and it shows up in `--help` next boot.

## Pre-commit hooks

```bash
pre-commit install         # once per clone
pre-commit run --all-files # manual run
```

Hooks run ruff + eslint + a conventional-commit message check. Run
once per clone — CI rejects PRs whose commits don't follow
`feat: / fix: / chore: / docs: / refactor: / test:` prefixes.
