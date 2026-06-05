# SenHarness

> **Production-ready, multi-tenant runtime for enterprise AI agents.** One binary serves a single team on-prem (`docker compose up`) or thousands of tenants in SaaS — under MIT license, no AGPL trap, no patent clauses, no "open core" gatekeeping. The whole runtime is in this repository.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org)
[![Next.js 15](https://img.shields.io/badge/Next.js-15-black)](https://nextjs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.117+-009688.svg)](https://fastapi.tiangolo.com)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/senweaver/SenHarness/pulls)

[English](./README.md) · [简体中文](./README_zh-CN.md)

---

## What you get in 90 seconds

🚀 **"I just registered → I have a workspace, an agent, and a chat in 30 seconds."**

Registration auto-provisions a personal workspace with a default agent. No setup wizard, no admin handshake, no "contact sales" email loop. Slug clashes resolve automatically; per-identity quotas and rate limits keep abuse out without slowing real users down. Email verification is opt-in, OAuth slots in next to it, and the platform-admin can flip registration to invite-only with a single switch.

🧠 **"My agent learns my preferences across sessions and grows skill packs by itself."**

A 12-dimension user profile builds over time from the runs you actually do. A separate evolver agent watches what worked and what failed, drafts skill updates with cited evidence, and submits them to an approval queue — never silent changes. A nightly curator archives unused skills, a verifier replays past runs against every candidate before activation, and pinned skills are exempt from automated archival forever.

🛡️ **"When a model goes down, my agent doesn't."**

Provider failover keeps the prompt-cache prefix stable across providers, so swapping a backend model doesn't burn tomorrow's cache. A sub-agent zombie reaper, run heartbeats, checkpoint recovery, an MCP keepalive watchdog, and an inflight-run reaper survive backend restarts and flaky tool servers. Risky operations are default-deny with approval queues; nothing privileged moves without an audit row you can grep.

---

## See it in action

<p align="center">
  <img src="assets/screenshot-chat.png" alt="Chat with goal lock" width="720">
</p>
<p align="center"><b>Chat with goal lock</b><br>Slash commands (<code>/goal</code>, <code>/insights</code>), per-message alignment scoring against a locked north-star, and an evidence trail every assistant turn can link back to.</p>

---

## Why SenHarness

| Concern | Other tools | SenHarness |
|---|---|---|
| **Multi-tenancy** | Single-tenant first; tenant logic bolted on later | Every domain row carries `workspace_id`. Tenant isolation is a hard constraint, not an aspiration |
| **Skill management** | Author writes once, hopes it stays useful | 9-state lifecycle + immutable version snapshots + nightly curator + auto-verifier replay before activation |
| **Self-evolution** | Manual updates only | Evolver agent proposes patches with cited evidence; admin approves; nothing changes silently |
| **Channel coverage** | One IM platform | 10+ channels: Slack · Discord · Lark/Feishu · WeChat · Telegram · Microsoft Teams · DingTalk · WeCom · QQ · generic webhook |
| **Reliability** | "It usually works" | Sub-agent zombie reaper · provider failover (cache-prefix-safe) · checkpoint recovery · MCP keepalive · cache-aware memory writes |
| **Audit & compliance** | Hidden in logs | Every state transition emits a stable audit action key. Full lineage from skill version → run → message. GDPR cascade soft-delete on identity / workspace removal |
| **Observability** | Print statements | Runtime console · background-job dashboard · lineage replay · cross-session insights · per-event notification preferences |
| **Approvals** | "Press y/n in chat" | Stable resource-typed approvals with per-resource TTL, default actions on expiry, and a notification chain for both requestor and approver |
| **Secrets** | `.env` everywhere | Envelope-encrypted vault with pluggable keyring (env · file · passphrase · AWS KMS · GCP KMS · Azure Key Vault · HashiCorp Vault) |
| **Plugins** | "Drop-in any Python file" | ed25519-signed bundles, capability scopes, platform-admin approval queue, default-OFF master switch |
| **License** | AGPL or "open core with patent traps" | MIT. Take it, ship it, fork it, no clauses |

---

## Quick start

```bash
git clone https://github.com/senweaver/SenHarness.git
cd SenHarness
cp .env.example .env             # fills sensible defaults; set at least one LLM key
docker compose up -d             # full stack: postgres · redis · backend · frontend · worker
open http://localhost:3000       # register → instant workspace + chat
```

Three commands. Three minutes. You're chatting with an agent on your laptop.

**Already have an OpenAI- or Anthropic-compatible client?** Point its `base_url` at `http://localhost:3000/api/v1/openai` and call `/v1/chat/completions`, `/v1/messages` (Anthropic format), or `/v1/responses` (OpenAI Responses format). Same workspace credentials, same audit trail, streaming + tool use + vision + file attachments all carry through. Drop-in for Claude Code, OpenAI Codex CLI, or anything else that speaks the protocol.

**Need help on the host?** `make logs` tails everything, `make sh-backend` drops you into the backend container, `make migrate` runs Alembic, `make seed` rebuilds the default workspace, and `make create-admin` mints a platform admin. Run `make test` for the full pytest + vitest matrix; `make lint` for ruff + eslint; `make typecheck` for ty + tsc.

**Going to production?** `docker compose -f docker-compose.prod.yml up -d` swaps in Traefik with TLS termination, hardened networking, and a worker process pool. Set `ENVIRONMENT=production` in `.env` and the backend will refuse to boot with insecure defaults — no JWT secret, dev-mode sandbox kind, plaintext keyring, or unset DB password will all halt startup with a clear error pointing at the offending field.

---

## What you can do

### 🏢 Build your team's collective AI brain

Agents learn skills from past runs. The evolver agent reviews what worked and what failed, then proposes new skill drafts with cited evidence — admin approves, the library grows itself.

- **Cross-workspace federation** is opt-in, sanitized (PII / emails / URLs / workspace slugs scrubbed), and gated by a 30-day human approval window.
- **Subscriber workspaces** pull updates as `PROPOSED` candidates that still go through the local verifier before activation.
- **Pinned skills** are exempt from automated archival forever, even when the curator votes them stale.

### 💬 Embed AI in your IM stack

One agent definition. 10+ delivery channels: Slack, Discord, Lark/Feishu, WeChat, Telegram, Microsoft Teams, DingTalk, WeCom, QQ, and generic webhooks.

- **Cross-platform session continuity** — a conversation started in WeChat continues on Web with the same memory, skills, and audit chain.
- **Default-on security** — per-channel HMAC, sender allowlists, replay windows, and rate buckets are on out of the box.
- **Custom channels** plug in through a registry; an 11th channel is one adapter file plus a vault entry.

### 🤝 Coordinate a multi-agent squad

Switch a session to `kind=squad` and a coordinator agent dynamically mounts squad members as sub-agents. One parent, N children, isolated retry budgets, one shared spine for telemetry.

- **Bounded fan-out** — `delegate_batch` parallelises sub-agents with per-branch concurrency caps.
- **Reliability gates** — heartbeats, a zombie reaper, and a hallucination-review approval queue before risky tool calls land.
- **Project boards** — workspace- and squad-level kanban track what every sub-agent is actually shipping.

### 🔌 Drop-in backend for any OpenAI- or Anthropic-compatible client

Point an existing client's `base_url` at `http://localhost:3000/api/v1/openai` and you get `/v1/chat/completions`, `/v1/messages` (Anthropic), and `/v1/responses` (OpenAI Responses) on day one. WebSocket streaming, tool use, vision, and file attachments all carry through.

- **One audit trail** — same workspace credentials, same audit chain whether the request comes from the UI or `curl`.
- **Two-Model-ID** — clients see a stable served name while you swap the upstream model without breaking the prompt cache.
- **Plug-and-play** for Claude Code, OpenAI Codex CLI, or anything else that speaks the protocol.

### ⏰ Run scheduled monitoring tasks (no-agent mode)

Cron flows in `no_agent_script` or `no_agent_http` mode let you say *"every morning at 09:00, ping our SLA dashboard, escalate to the agent only on failure."* 99% of those checks burn zero LLM tokens.

- **Vault-backed credentials** — `${vault://workspace/<key>}` interpolates into HTTP headers and bodies at run time.
- **SSRF pinning** resolves DNS once and rejects private IPs by default.
- **Production guardrails** — script-mode flows refuse to run with `sandbox.kind=local` in production; SSH backend with command allowlist is the supported path.

### 🛠️ Connect to MCP tool servers

Three transports — stdio, SSE, Streamable HTTP — with OAuth client-credentials built in. Image, audio, and file results pass through as first-class parts.

- **Per-server keepalive** plus concurrency caps mean a misbehaving tool server can't take down the whole worker pool.
- **Vault-sealed OAuth tokens** rotate automatically on expiry; the workspace never sees the bearer in plaintext.
- **Zero-glue setup** — paste the MCP endpoint URL and an optional client-id; the first tool call begins the audit trail.

### 📚 Ground answers in a workspace knowledge base

Built-in KB connectors ingest URLs, files, and S3 buckets with document-level ACLs and SSE-streamed sync progress. Knowledge is workspace-scoped just like agents and skills.

- **Pluggable connectors** — write a new source via `register_connector` (same registry pattern as channels).
- **Tight ACLs** — every document carries a workspace + owner row; cross-workspace reads require an explicit platform-admin path.
- **Live progress** — sync jobs stream status over SSE so the UI never lies about whether a crawl is done.

### 🛡️ Run audited self-evolving agents in production

Every skill change, every job retry, every memory write, every notification — all flow through stable audit keys you can grep. Default-deny on dangerous operations.

- **Approval queues** for risky changes with per-resource TTLs that escalate to admin before expiry.
- **GDPR cascade** soft-delete on user or workspace removal, retention watermarks, and an opt-in physical-purge ARQ task.
- **Platform-admin settings** — schema-driven forms with `.env`-override badges and dangerous-change confirmations.

### 🩺 Debug and replay long-running sessions

A runtime console lists every inflight run across the workspace, exposes provider routing and heartbeats, and force-recycles stuck runs without restarting the backend. Lineage replay turns compressed summary messages back into the original turns that produced them.

- **Job observability** — ARQ task lifecycle dashboard with manual retry and failure clustering.
- **Skill knowledge graph** — `derived` / `supersedes` / `fork` / `hub-pull` edges visualised; click through to any node.
- **Trace replay** — every artifact links back to the run, the message, and the skill version that produced it.

### 🧠 Get cross-session insights from your own runs

`/insights --days 30` asks *"based on my last month, where do I keep getting stuck?"* The auxiliary LLM clusters error kinds, tools that misfired, and skills that helped, then renders a summary with links back to the supporting sessions.

- **Privacy by default** — even a workspace admin only sees clusters from their own runs; no cross-identity peek path.
- **Always-on fallback** — a heuristic clusterer kicks in when the auxiliary LLM is unavailable, so the command never returns empty.
- **Evidence trail** — every insight links back to the artifacts that triggered it.

---

## What's in the box

| Surface | Built-in adapters |
|---|---|
| **Model providers** | OpenAI · Anthropic · Google · xAI · OpenRouter · Azure OpenAI · HuggingFace · DeepSeek · DashScope · Bailian · Moonshot · Kimi Code · Zhipu · SiliconFlow · MiniMax · Ollama · vLLM · custom |
| **Agent backends** | `native` (in-process) · `openclaw` (remote worker) · `protocol_kernel` (provider passthrough) — all behind the same `AgentBackend` protocol |
| **IM channels** | Slack · Discord · Lark/Feishu · WeChat · Telegram · Microsoft Teams · DingTalk · WeCom · QQ · generic webhook |
| **MCP transports** | stdio · SSE · Streamable HTTP (with OAuth client-credentials) |
| **Sandbox kinds** | `local` (dev only) · `docker` · `state` · `ssh` (opt-in; vault-backed keys + known-hosts pinning + command allowlist) |
| **Keyring backends** | env · file · passphrase · AWS KMS · GCP KMS · Azure Key Vault · HashiCorp Vault |
| **Compatibility surfaces** | OpenAI Chat Completions · Anthropic Messages · OpenAI Responses · WebSocket streaming · IM webhook ingress |
| **Knowledge base connectors** | url · file · s3 · custom (via `register_connector`) |
| **Schedulers & job runners** | APScheduler cron (Redis leader election) · ARQ worker queues · cron slot map shipped in docs |
| **Notification transports** | in-app inbox + WebSocket push · email (SMTP / log transport) · quiet hours + per-event preferences |
| **Audit sinks** | PostgreSQL `audit_events` (default) · pluggable forwarder via plugin (capability-scoped, write-only) |
| **Approval resources** | `tool_call` · `skill_pack` {create / patch / edit / delete / archive / write_file / remove_file} · `flow_create` · `subagent_hallucination_review` · `hub_promotion` |
| **Evaluation / aux-LLM tasks** | goal alignment · run-quality judge · evolver proposal · cross-session insights · sub-agent hallucination gate · reflection hook |
| **Plugin extension points** | ed25519-signed bundles · 6 lifecycle hooks (`on_session_start/end`, `pre/post_llm_call`, `pre/post_tool_call`) · `register_model_provider` / `register_channel_kind` / `register_hook` · platform-admin approval queue |

Adding an entry to any row is one adapter file plus tests. Built-in kinds can never be overwritten by a plugin — the registry refuses on register-time and writes an audit row, so a hostile drop-in cannot silently substitute the `slack` channel.

---

## Architecture

SenHarness ships as one Python 3.12 + FastAPI backend, a Next.js 15 + React 19 frontend, PostgreSQL (with pgvector) for state, Redis for queues / locks / rate limits, and an ARQ worker. Same image, same `docker-compose.yml`, single binary for dev and prod.

The runtime is six conceptual layers — end of story:

- **Context** — skills, memory, tools, and a locked goal. Cache-aware writes defer memory edits to "next session" so today's prompt cache stays warm.
- **Tools** — built-in tools, MCP servers, and signed plugins, with ACL / budget / approval gate around every call.
- **Execution** — run loop, sub-agent batching, provider routing, checkpoint recovery, heartbeats. Inflight runs survive backend restarts via a recovery sweeper.
- **Memory** — per-turn artifacts, session summaries, workspace memory, a 12-dimension user profile, and immutable lineage that never breaks trace replay.
- **Evaluation** — quality judge scores, alignment to the locked goal, auto-verifier replays. Aux-LLM calls sit behind a circuit breaker.
- **Constraints & Recovery** — approvals, shields, sandbox policy, provider failover, keyring-backed vault, channel security. Default-deny on dangerous ops, audit-on-write on everything else.

Around that core: skills are versioned markdown bundles with a 9-state lifecycle; channels and providers are registry-pluggable; plugins ship signed (ed25519) and gated by a platform-admin approval queue with capability scopes; admin settings expose every knob behind schema-driven forms with `.env`-override badges. Agent runs flow through the unified `AgentBackend` protocol so the inference library is swappable; the MCP transport is the official Python SDK.

---

## A typical day

A user types into a chat; the backend resolves workspace, picks the agent, builds context, routes through the configured `AgentBackend`, and streams the response over WebSocket while capturing a session artifact for asynchronous quality scoring. Tool calls checkpoint the run, sub-agents get isolated heartbeats and retry budgets, and a 503 from one provider rolls over to the next without breaking the cache prefix. Overnight, the curator sweeps stale skills, the evolver clusters recent failures into skill-update proposals for the approval queue, and the platform-admin dashboard shows queue depth, retry rates, and lineage at a glance — day-one behaviour, not future work.

---

## FAQ

**Is it production-ready?**

Yes. Audit-on-write, default-deny dangerous ops, sub-agent zombie reaping, inflight-run recovery, GDPR cascade soft-delete, a vault-backed keyring with seven providers, ed25519-signed plugins, and a hardened production compose file — we dogfood it.

**Can I run it on a single VM?**

Yes. `docker compose up -d` brings the full stack on a 4 GB / 2 vCPU box. Postgres + Redis ride alongside the backend; persistent volumes are bind-mounted so `down` + `up` keeps your data.

**Does it work offline / air-gapped?**

Yes. Once images are pulled, backend and frontend run fully offline. Bring your own model provider (Ollama or vLLM counts), disable email fan-out in platform settings, and keep federation / plugin-signing root keys on a thumb drive.

**Can I bring my own model?**

Yes. The provider catalog is pluggable with 17+ bundled adapters. The two-model-id pattern means agents see a stable served name while you swap the upstream model without breaking the prompt cache.

---

## Community

⭐ **Star us** if SenHarness saves your team time — it's the cheapest thank-you we'll ever ask for.

🐛 **Issues / feature requests** — [GitHub issues](https://github.com/senweaver/SenHarness/issues) with the `bug` or `enhancement` label.

🛠️ **Pull requests** — [open a PR](https://github.com/senweaver/SenHarness/pulls). Conventional Commits + pre-commit hooks shipped in repo.

---

## License

MIT — see [LICENSE](LICENSE).

[![Contributors](https://contrib.rocks/image?repo=senweaver/SenHarness)](https://github.com/senweaver/SenHarness/graphs/contributors)
