# SenHarness Architecture

> Organised by the six layers of a Harness (L1–L6). For the paradigm itself, see [harness-engineering.md](harness-engineering.md).

---

## Positioning

SenHarness is the **Harness Engineering runtime for enterprises** — MIT licensed, multi-tenant, with a pluggable Agent Runtime. It wraps the model (any LLM provider) in a six-layer control system so that long-running, high-stakes AI agent work is safe, observable, and auditable.

One codebase, two deployment modes:

- **Self-hosted single-company** — one workspace, or a few split by department / BU
- **Multi-tenant SaaS** (future — *SenHarness Cloud*) — physical isolation via `workspace_id` scoping on every table

---

## System overview

```
┌──────────────────────────── Consumer surface ────────────────────────────────┐
│  Home · side navigation (recent agents) · chat · skills · knowledge           │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │
┌───────────────────────── Admin console (/settings/*) ────────────────────────┐
│  members · roles · departments · model pool · MCP · policies · approvals     │
│  budgets · audit · channels · vault · keyring · agent runtimes · skills      │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │
┌──────────────────── The six-layer Harness (this document) ───────────────────┐
│ L1  Context Governance    ──►  persona, memory fragments, system prompt      │
│ L2  Tool System           ──►  toolsets, MCP, tool guards, plugin hooks      │
│ L3  Execution Orchestration ► agent loop, iteration budget, sub-agents, flows│
│ L4  Memory & State        ──►  memories, checkpoints, attachments            │
│ L5  Evaluation & Observ.  ──►  Logfire / OTel / Langfuse / audit             │
│ L6  Constraints & Recovery──►  shields, approvals, sandbox, retry / rollback │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │
┌──────────────────── Pluggable Agent Runtime (AgentBackend) ──────────────────┐
│    NativeRuntime (native)   │   OpenClaw remote   │   custom backends        │
└──────────────────────────────────────────────────────────────────────────────┘
                                     │
         LLM providers · MCP servers · tool endpoints · sandboxes
```

---

## Technology stack

- **Backend**: FastAPI · SQLAlchemy 2 async · Pydantic 2 · Alembic · PostgreSQL + pgvector · Redis · ARQ · APScheduler
- **Agent core**: pydantic-ai ecosystem — `pydantic-ai`, `pydantic-ai-harness[code-mode]` (Monty sandbox), `pydantic-ai-middleware`, `summarization-pydantic-ai`, `subagents-pydantic-ai`, `pydantic-ai-skills`, `pydantic-ai-shields`, `pydantic-ai-backends`, `pydantic-ai-todo`
- **Frontend**: Next.js 15 · React 19 · shadcn/ui · TanStack Query · Zustand · next-intl · Tailwind 4
- **Deploy**: Docker · docker-compose · Traefik (prod) · Kubernetes (optional)

---

## L1 · Context Governance

> **Role · goal · success criteria. Slice information. Keep rules / task / state / evidence separate.**

Every agent run starts with carefully-assembled context, not a raw dump. Components:

- **Persona**: `Agent.persona_md` — role, tone, boundaries
- **Branding terminology**: workspace-configurable UI label (`assistant` / `digital employee` / `agent` / ...) via `workspace.branding_json.agent_term`
- **System prompt assembly**: [`backend/app/agents/prompts.py`](../backend/app/agents/prompts.py) merges persona + current time + memory fragments + skill hints
- **Memory injection**: at run start, the top-K workspace / identity memories (via vector similarity + token overlap) are rendered to a markdown block and prepended to the system prompt (see L4)
- **Sliding-window context compression**: `SlidingWindowProcessor(trigger=150 msgs, keep=60)` wired through `summarization-pydantic-ai`

**V2 additions** — repo context injection (auto-load workspace `AGENTS.md` / `README` / project docs for coding agents), structured layering (rules / task / state / evidence rendered as discrete sections instead of concatenated prose).

---

## L2 · Tool System

> **Which tools, when to call, how results flow back.**

Turning the model from a text predictor into an actor. Components:

- **Built-in toolset**: [`backend/app/agents/tools/`](../backend/app/agents/tools/) — 10 tools (echo, current_time, calculator, filesystem, knowledge, memory, multimedia, web_search, web_fetch)
- **Tool registry**: auto-discovery via import-time registration (`BuiltinTool` entries)
- **Per-agent binding**: `Agent.toolbox_id` + future `tool_bindings` table (V2) for MCP + plugin tools
- **MCP native support** (V2): `mcp_servers` + `toolboxes` + `tool_bindings` tables · `/api/v1/mcp/*` routes · health checks
- **Plugin host**: `HarnessPluginHost` provides 11 hooks — pre/post tool, pre/post LLM, pre/post API, on_session_*, transform_* — implemented via `pydantic-ai-middleware`

**Tool access control** belongs partly here and partly at L6 (see `shields.tool_guard` which enforces the ACL and emits approval requests).

---

## L3 · Execution Orchestration

> **Understand goal → decide if info sufficient → gather → analyse → generate → check → fix/retry.**

How work actually gets done. Components:

- **Main agent loop**: [`backend/app/agents/kernels/native/runner.py`](../backend/app/agents/kernels/native/runner.py) — streaming via `pydantic_ai.Agent.iter()` with normalized `RunEvent` output (`delta`, `thinking`, `tool_call`, `tool_result`, `approval_update`, `usage`, `error`, `final`)
- **Iteration budget**: `IterationBudget` caps tool-call loops; prevents runaway agents
- **Sub-agents** (delegation): `SubAgentCapability` via `subagents-pydantic-ai` — task/check_task/answer_subagent/wait_tasks/cancel tools
- **Squad mode**: `sessions.kind=squad` routes to the coordinating agent which dynamically wires squad members as sub-agents
- **Visual Flow engine**: `services/flow_engine.py` — topological sort DAG execution with 4 node kinds (start, agent_call, http_request, end)
- **CodeMode**: `pydantic-ai-harness[code-mode]` with Monty sandbox — lets the model write Python for complex tool chains instead of N sequential tool calls

**V2 additions** — Planning (structured task decomposition before execution), Verification Loop (edit → run tests → auto-fix feedback cycle for coding agents), Tool Search (progressive tool discovery for large toolsets via pydantic-ai `deferred_loading`).

---

## L4 · Memory & State

> **Task state, intermediate results, long-term memory, user preferences.**

### Current (V1)

- **`memories` table** — scope (`user` / `assistant` / `workspace`) × kind (`kv` / `episodic` / `semantic`)
- **pgvector embeddings** (1024-dim) with a 3-tier fallback (OpenAI → Ollama → deterministic hash)
- **Retrieval**: cosine similarity + token-overlap scoring; top-6 with `min_score=0.30` auto-injected to system prompt each turn
- **`session_checkpoints` table** (D21) — `capture_checkpoint` / `fork_at_checkpoint` for rewind and branching
- **Attachments** — files with content-addressable storage + MIME classification + path-traversal protection

### V2 — four-layer memory (workspace-aware)

| Layer | Purpose | Mechanism |
|---|---|---|
| L1 · Semantic | "What I know" | per-workspace `MEMORY.md` + per-identity `USER.md` with character caps, injected in full at session start |
| L2 · Episodic | "What I did" | Postgres `tsvector` + pgvector, exposed via `session_search` tool (on-demand retrieval) |
| L3 · Procedural | "What I can do" | `skill_packs` table + progressive loading (manifest always loaded; full content on-demand) |
| L4 · User modelling | "Who you are" | per-identity `SOUL.md` with passive 12-dimension accumulation + approval-gated writes |

This matches the pattern of **limited memory + active curation** rather than unbounded retention.

---

## L5 · Evaluation & Observability

> **Output acceptance, env verification, automated tests, logs, root-cause analysis.**

### Current (V1)

- **Observability**: Logfire + OpenTelemetry instrumentation (FastAPI + SQLAlchemy) + Langfuse + Sentry
- **Audit events**: `audit_events` table + 13+ audit hooks across auth / agents / squads / approvals / clone / report
- **Usage metrics**: `/metrics/usage` aggregates tokens and USD cost from a pricing catalog (40+ models)
- **Cost tracking**: realtime per-agent-run cost computation, persisted on the assistant message
- **Trace IDs**: every request gets a `X-Request-ID` propagated to responses

### V2 — the "AI cannot evaluate itself" principle

- **Independent evaluation agent** — a separate QA agent (via auxiliary LLM routing) grades primary-agent outputs
- **Semantic validation** — optional `semantix-ai` NLI checks on structured outputs
- **Trace replay UI** — step through a run's events visually
- **Prometheus metrics endpoint** — scrape-friendly counter/histogram for all 6 layers

---

## L6 · Constraints & Recovery

> **What can/can't be done. Output validation. Retry / downgrade / rollback.**

### Permission boundaries

- **Autonomy levels**: L1 (chat only), L2 (tool use), L3 (destructive — requires approval)
- **`shields.tool_guard`** — per-tool ACL; emits `approval_request` WebSocket frames for sensitive ops
- **Policy (V2 standalone tables)**: `policies` / `budgets` with three-tier override (global → workspace → agent)

### Sandbox isolation

- **Three kinds**: `docker` (per-session container) · `local` (filesystem scope) · `state` (in-memory, no shell)
- **Secure defaults** (V1 hardening):
  - `execute=False` by default — arbitrary shell is opt-in per agent
  - `ruleset="default"` — conservative command allowlist
  - `require_execute_approval=True` — every shell call goes through HITL
  - **Production guard**: `kind=local + execute=True` is refused in prod unless `SANDBOX_LOCAL_EXECUTE_PROD=true` is explicitly set. This prevents an agent from executing shell inside the SenHarness backend process itself.
  - CodeMode / Monty allowlist forbids reaching into SenHarness internals (`app.*`, `asyncpg`, `sqlalchemy`, `httpx`)

### Guardrails

- **`pydantic-ai-shields`** — input/output guardrails, PII detection, injection detection, secret masking, blocked keyword filtering
- **Budget tracking** — token and USD budgets with `on_exceed` action (warn / stop)

### Approval workflow (HITL)

- **`approvals` table** — pending / approved / denied / expired
- **WebSocket frames**: `approval_request` (server→client) + `approval_update` (both directions)
- **UI**: `/approvals` queue + in-chat approval card + bulk decide + department-aware routing + urgent preview bell
- **Cross-workspace admin**: platform admins can approve across workspaces for incident response

### Recovery (V2)

- **Stuck-loop detection** — N-turn repeated tool call pattern ⇒ break with SystemMessage
- **Tool error recovery** — per-tool retry with exponential backoff and a configurable error budget
- **Tool orphan repair** — dangling `tool_call_id` entries in message history get synthesized `tool_result` stubs so the loop can continue
- **Adaptive reasoning** — `ModelSettings.reasoning_effort` auto-scaled by task complexity
- **Tool output overflow** — large outputs truncate with a spill-to-disk fallback

---

## Cross-cutting: the pluggable Agent Runtime

The `AgentBackend` protocol (`backend/app/agents/kernels/base.py`):

```python
class AgentBackend(Protocol):
    backend_kind: str

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]: ...
    async def cancel(self, run_id: uuid.UUID) -> None: ...
    def capabilities(self) -> BackendCapabilities: ...
```

Registry: [`backend/app/agents/kernels/registry.py`](../backend/app/agents/kernels/registry.py) — import-time `register(backend)` keyed by `backend_kind`.

Official runtimes:

- **`native`** — in-process `NativeBackend` powered by `pydantic-ai-harness`. The default.
- **`openclaw`** — gateway + long-poll to remote workers (see below).

Writing a new runtime: [adding-features.md#agent-runtime-adapter](adding-features.md#agent-runtime-adapter) — 50 lines of Python.

---

## OpenClaw remote worker (D18)

The second Agent Kernel path — a remote worker runs on the user's own hardware (laptop, on-prem server, Raspberry Pi). SenHarness dispatches requests via a gateway; the worker long-polls for work, executes locally (possibly against its own LLM endpoint), and emits events back.

```
SenHarness WS / Runner ─┐
                        │ enqueue(RunRequest) ─► gateway_messages.request
                        ▼
                  ┌──────────┐          HTTP long-poll
                  │ gateway_ │ ◄─────────────────────┐
                  │ messages │                       │
                  └──────────┘                       │
                        ▲                            │
                        │ emit(seq, kind, data)      │
                        │ UNIQUE(run_id,dir,seq)     │
                        │ de-dupes replays           │
                        │              ┌──────────────────────┐
                        └──────────────│  Remote worker       │
                                       │  X-Api-Key auth      │
                                       └──────────────────────┘
```

- `Agent.backend_kind="openclaw"` agents must bind a `backend_adapters` row
- Remote worker calls `/api/v1/gw/openclaw/{register,poll,emit}` with `X-Api-Key`
- Hot-path auth via SHA-256 lookup; envelope-encrypted plaintext kept in Vault for rotation
- Cancellation: `OpenClawBackend.cancel` writes a `kind=cancel` request row; the worker picks it up on the next poll

---

## External agent protocol (roadmap)

Previous releases shipped a self-invented WebSocket frame protocol called
**SAIP** (`/api/v1/gw/saip/ws`). It has been removed — it was never a
public standard and external IDEs could not use it directly.

A future release will add the **Zed Agent Client Protocol**
(``spec.agentclientprotocol.com``, JSON-RPC 2.0) as the standard external
transport, mounted at `/gw/acp/ws`, so Zed / Cursor / custom SDKs can
drive SenHarness agents through a widely-implemented spec.

Inside the browser, the canonical interactive path remains the session
WebSocket at `/api/v1/sessions/ws/{session_id}` (Cookie-auth).

---

## Session run flow

```
FE ─► Channel gateway / WebSocket ─► SessionService ─► Kernel ─► AgentBackend
                                                    │
                                                    └─► Policy pre-check (L6)
                                                           │
                                                     tool_call / approval_request
                                                           │
                                                     Approval queue (HITL)
                                                           │
                                             stream RunEvents ────────► FE
```

---

## Multi-tenancy details

- **`workspace_id` scoping**: every business table has `workspace_id` (see `WorkspaceScopedMixin`)
- **`Identity` is global**: one identity → membership in N workspaces with different roles
- **`Membership`**: `(identity_id, workspace_id, role, department_id, status)` — the per-tenant join row
- **`workspace_type` tag** (V1 addition): `company` / `department` / `team` / `project` / `tenant` — used by the UI for labelling only, does not change RBAC
- **Cross-workspace admin**: platform admins have read/write on every workspace for incident response, with full audit of their actions
- **Single-company mode**: the workspace switch UI reduces to "here's your workspace"; everything else looks the same
- **SaaS mode** (future): the workspace switch UI becomes tenant-level navigation; row-level RLS on every query ensures physical isolation

See [harness-engineering.md §6](harness-engineering.md#6--multi-tenant-harness--why-it-matters) for the positioning rationale.

---

## Further reading

- [harness-engineering.md](harness-engineering.md) — the paradigm explained
- [adding-features.md](adding-features.md) — write a custom Agent Runtime / tool / channel / kernel / KB connector
- [quickstart.md](quickstart.md) — 10-minute single-company setup
- [deployment.md](deployment.md) — production hardening, sandbox choices, keyring providers
- [extensions-and-governance.md](extensions-and-governance.md) — channels · MCP · plugins · approvals · notifications · evaluation
- [runtime-and-jobs.md](runtime-and-jobs.md) — sessions · memory · judge · curator · evolver · reflection · lineage replay
- [skills.md](skills.md) — skill lifecycle · versions · hub · curator · verifier · lineage graph
