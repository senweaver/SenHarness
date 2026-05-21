# Quickstart — company deployment in 10 minutes

This guide gets a single company from zero to "employees chatting with
agents" in under ten minutes. Target audience: the IT person or
platform engineer doing the first deploy.

Everything below runs on a single Linux / macOS / WSL host with Docker
24+ and 4 GB free RAM. For a proper production deployment (TLS,
multi-host, high-availability) see [deployment.md](deployment.md).

---

## 1. Prerequisites (1 minute)

```bash
docker --version                    # 24 or higher
docker compose version              # v2 plugin
```

You'll also want **at least one LLM API key** so agents can actually
respond. Any of these work:

- OpenAI (`OPENAI_API_KEY`)
- Anthropic (`ANTHROPIC_API_KEY`)
- DeepSeek / Moonshot / Groq / OpenRouter
- Ollama running locally (free; slower first response)

---

## 2. Clone + configure (2 minutes)

```bash
git clone https://github.com/senweaver/SenHarness.git
cd SenHarness
cp .env.example .env
```

Open `.env` and set **at minimum**:

```bash
# Security — generate strong random values; SenHarness refuses to
# start in APP_ENV=production with the dev defaults.
JWT_SECRET_KEY=$(openssl rand -base64 48)
DB_PASSWORD=$(openssl rand -base64 24)
REDIS_PASSWORD=$(openssl rand -base64 24)
SENHARNESS_MASTER_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(48))')

# At least one LLM key
OPENAI_API_KEY=sk-...
```

Tip: if you run `openssl`/`python` inline, just paste the output into
the respective line.

---

## 3. Boot the stack (3 minutes)

```bash
docker compose up -d
```

This starts five containers: backend (FastAPI), worker (ARQ),
scheduler (APScheduler + Redis leader lease), Postgres+pgvector,
Redis, and the Next.js frontend.

Wait for the health checks to go green:

```bash
docker compose ps
```

All services should show `healthy` (backend takes the longest — it
runs Alembic migrations on first boot).

---

## 4. Bootstrap the first admin (2 minutes)

```bash
docker compose exec backend python -m cli.commands create-admin
```

Follow the prompts for email + name + password. This account becomes
the platform administrator with access to every workspace.

Seed a demo workspace with example agents and knowledge:

```bash
docker compose exec backend python -m cli.commands seed
```

---

## 5. First login + first chat (2 minutes)

Open `http://localhost:3000`.

1. Sign in with the admin credentials you just created.
2. You should land on the Home screen. The left sidebar shows one
   default agent (`SenHarness 助手`).
3. Type anything into the prompt and press Enter. You should see
   the streaming response character-by-character.

If the stream doesn't start, check `docker compose logs backend` —
the most common cause is a missing or invalid LLM API key.

---

## 6. What to try next (optional)

- **Invite a coworker**: `/settings/workspace/members` → *Invite* →
  share the generated link.
- **Create your own agent**: `/agents/new`. The "Runtime" picker
  reads from `/api/v1/agents/runtimes` — pydantic-ai (native) is
  the default.
- **Upload a knowledge base**: `/knowledge`. Paste text or drag a
  PDF; the agent can query it during chat.
- **Set an approval policy**: `/settings/policies`. Dangerous tool
  calls (file writes, shell execution) now wait for human approval
  in the `/approvals` queue.

---

## Common issues

**"Refusing to start in production"**
You set `APP_ENV=production` but left one of
`JWT_SECRET_KEY` / `DB_PASSWORD` / `REDIS_PASSWORD` /
`SENHARNESS_MASTER_KEY` at its dev default. Re-run the `openssl`
generators and retry.

**"sandbox.local_execute_blocked_in_prod"**
An agent is configured with `metadata.sandbox = {"kind": "local",
"execute": true}` and you're in production. SenHarness refuses this
combination because it would run arbitrary shell inside the backend
container. Switch the agent to `kind: docker` or `state`; see
[deployment.md · Agent sandbox](deployment.md#agent-sandbox).

**Frontend shows "Cannot reach backend"**
The `NEXT_PUBLIC_API_BASE_URL` baked into the frontend image is
probably wrong. For custom deploys rebuild with
`--build-arg NEXT_PUBLIC_API_BASE_URL=https://your-domain`.

---

## Upgrading

```bash
git pull
docker compose up -d --build
```

The backend entrypoint runs `alembic upgrade head` automatically on
every boot, so you don't need a separate migration step.

---

More docs:

- [architecture.md](architecture.md) — SenHarness's six-layer Harness
- [adding-features.md](adding-features.md) — write a custom Agent Runtime / tool / channel
- [deployment.md](deployment.md) — production hardening + HA
- [harness-engineering.md](harness-engineering.md) — the paradigm
