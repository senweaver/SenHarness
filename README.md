<div align="center">

# SenHarness

**An open-source, multi-tenant implementation of the Harness Engineering paradigm for enterprise AI agents.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Next.js 15](https://img.shields.io/badge/next.js-15-black.svg)](https://nextjs.org/)
[![FastAPI](https://img.shields.io/badge/fastapi-0.117%2B-009688.svg)](https://fastapi.tiangolo.com/)

[English](README.md) · [简体中文](README_zh-CN.md)

</div>

---

## What is SenHarness?

If you've ever watched an AI agent run for 30 minutes, forget its own plan, burn through your budget, and hallucinate a fix to a problem that never existed — you know why **the model alone is not enough**.

**Harness Engineering** is the discipline of designing everything *outside* the model: the file system, sandboxed shell, memory, retrieval, context governance, orchestration, guardrails, approvals. It's the agent's *harness* — the reins that keep it on track.

SenHarness is **the Harness Engineering runtime for enterprises**. One code base, two deployment modes:

- **Self-hosted single-enterprise** — one company, `docker compose up`, done
- **Multi-tenant SaaS** — same binary, many tenants, physical isolation

All under **MIT license**.

---

## The Harness, in one line

SenHarness ships six layers around the model — context · tools · orchestration · memory · evaluation · constraints — so the agent stays on plan, on policy, and on budget.

---

## What makes SenHarness different

1. **Pluggable Agent Runtime**.  The `AgentBackend` protocol + gateway means any runtime — the bundled `NativeBackend`, OpenClaw remote workers, or a future agent framework — ships as a single adapter file.
2. **Multi-tenant from day one**. Every table is `workspace_id`-scoped. The same binary serves a single company on-prem or thousands of tenants in SaaS. No rewrite when you scale.
3. **MIT license**. Take it, ship it, fork it, build on it. No patent clause gotchas, no AGPL copyleft. The enterprise edition adds compliance packs and SLAs on top — the core stays free.

Under the hood: PostgreSQL + pgvector · Redis · FastAPI · SQLAlchemy 2 async · Next.js 15 + shadcn/ui · and the `pydantic-ai` ecosystem (harness · shields · backends · skills · subagents · todo · middleware · summarization).

---

## Who is this for?

| You are... | SenHarness gives you... |
|---|---|
| An **employee** at a company that deploys it | A personalized AI assistant that remembers your preferences, respects company rules, and asks for approval on sensitive operations |
| A **company admin** | Workspace setup in 10 minutes — invite members, configure LLM providers, wire up Slack/Feishu, set up audit trails |
| A **developer / platform engineer** | A Harness layer you can extend: plug new tools via MCP, write a custom Agent Runtime adapter, ship your own skill pack |
| An **enterprise IT buyer** | Multi-tenant RBAC · audit · HITL approval · vault with envelope encryption · pluggable keyring (env / file / passphrase / AWS KMS / GCP KMS / Azure KV / Vault) |

---

## Quick start

**Prereqs**: Docker 24+ · 4 GB free RAM · any LLM API key (OpenAI / Anthropic / DeepSeek / Ollama...)

```bash
git clone https://github.com/senweaver/SenHarness.git
cd SenHarness
cp .env.example .env
# Edit .env: set JWT_SECRET_KEY, DB_PASSWORD, REDIS_PASSWORD, plus at least one LLM key

docker compose up -d

# First-time setup (inside the backend container)
docker compose exec backend python -m cli.commands migrate
docker compose exec backend python -m cli.commands seed
docker compose exec backend python -m cli.commands create-admin

# Open http://localhost:3000
```

---

## Core concepts

| Concept | Description |
|---|---|
| **Workspace** | A tenancy / governance boundary. Hosts members, roles, departments, agents, knowledge, policies, audit. Use one workspace for a single company, multiple for a group or SaaS. |
| **Department** | Organizational tree inside a workspace. Any depth. Used for ownership, approval routing, role-based search. |
| **Identity** | A global account (email + password + optional OAuth + optional TOTP). One identity → membership in N workspaces with different roles. |
| **Agent** | The configurable unit an employee talks to. UI label configurable per workspace (assistant / digital employee / AI partner / ...). |
| **Squad** | A group of agents working together under a coordinating agent. |
| **Skill Pack** | Anthropic Agent Skills compatible SKILL.md — progressive loading. |
| **Toolbox** | Built-in + MCP + plugin tools, with per-agent binding. |
| **Flow** | Scheduled or triggered automation (cron · webhook · on_message · manual). |
| **Channel** | IM integrations (Slack · Feishu · Discord · generic webhook + more in V2). |
| **Vault** | Envelope-encrypted credential store, pluggable keyring. |
| **Policy** | Autonomy level (L1/L2/L3) · shields · budgets · tool ACL · approval workflows. |
| **Agent Runtime** | Pluggable execution backend. |

---

## Pluggable Agent Runtime

The `AgentBackend` protocol lives in [`backend/app/agents/kernels/base.py`](backend/app/agents/kernels/base.py):

```python
class AgentBackend(Protocol):
    backend_kind: str
    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]: ...
    async def cancel(self, run_id: uuid.UUID) -> None: ...
    def capabilities(self) -> BackendCapabilities: ...
```

Officially supported runtimes:

| Runtime | Kind | Transport | Status |
|---|---|---|---|
| **NativeBackend** | `native` | In-process | ✅ Stable |
| **OpenClaw remote worker** | `openclaw` | Gateway + long-poll over HTTPS | ✅ Stable |
| *Your custom runtime* | `your-backend` | *anything* | Community-supplied |

## Built-in channel providers

Channels are the second pluggable layer — they bring IM platforms
into SenHarness. Same registry pattern as Agent Runtimes.

| Provider | Kind | Inbound auth | Outbound | Status |
|---|---|---|---|---|
| Slack | `slack` | v0 HMAC + 5-min replay window | chat.postMessage | ✅ |
| Feishu / Lark | `feishu` | verification_token (1.0/2.0) | Open API tenant token | ✅ |
| Discord | `discord` | Ed25519 | Discord REST API | ✅ |
| DingTalk (钉钉) | `dingtalk` | HMAC-SHA256 + 60-sec replay window | custom-robot webhook | ✅ V2 |
| WeCom (企业微信) | `wecom` | SHA1 message-signature + AES-CBC payload | message/send REST | ✅ V2 |
| Generic webhook | `webhook` | shared `inbound_token` only | — (inbound-only) | ✅ |
| *Your provider* | `your_kind` | *your call* | *your call* | Community-supplied |

---

## Repository layout

```
SenHarness/
├─ backend/              FastAPI + SQLAlchemy + pydantic-ai
│  ├─ app/
│  │  ├─ agents/         Agent core (kernels / harness / tools / skills)
│  │  ├─ api/            REST + WebSocket routes
│  │  ├─ core/           config / security / middleware / rate limit / errors
│  │  ├─ db/             async engine / models / repositories
│  │  ├─ schemas/        Pydantic DTOs
│  │  ├─ security/       JWT / keyring / envelope encryption
│  │  ├─ services/       business services
│  │  └─ workflows/      flows / triggers / scheduler
│  ├─ tests/             unit / integration / e2e
│  └─ scripts/           ops / dev / probes
├─ frontend/             Next.js 15 · shadcn/ui · Tailwind 4
├─ docs/                 architecture · adapters · whitepapers · quickstart
└─ docker-compose*.yml   dev / prod / frontend-only compositions
```

---

## Contributing

SenHarness welcomes contributions. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, coding standards, and how to get your adapter listed in the official registry.

---

## License

[MIT](LICENSE) © 2026 SenHarness Contributors

---
