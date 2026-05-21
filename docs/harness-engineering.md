# Harness Engineering — A Paradigm for Reliable AI Agents

> *"The model's ceiling is not inside the model. It's outside."*

This document introduces **Harness Engineering** — the discipline of designing everything around the model — and explains how SenHarness implements it as a multi-tenant, open-source runtime.

Audience: architects, platform engineers, and technical buyers evaluating SenHarness or thinking about how to operate AI agents at scale.

---

## 1 · Why "Harness"?

A horse without a harness is dangerous — fast, powerful, and going wherever it feels like. A horse *with* a harness is useful — still fast and powerful, but it goes where you aim it, stops when you pull the reins, and keeps pulling the same direction you do.

AI agents are the same. The model is the horse. The Harness is the reins, the saddle, the bridle — **everything that turns raw capability into reliable, directable, safe work.**

Concretely, a Harness contains:

- **File system** — where the agent reads and writes
- **Sandboxed shell** — how the agent executes code safely
- **Memory** — what the agent remembers across turns and sessions
- **Retrieval** — how the agent pulls in documents, code, context
- **Context engineering** — how that context is assembled, sliced, and presented
- **Orchestration** — how tasks are decomposed, dispatched, verified
- **Guards** — what the agent may and may not do
- **Approvals** — when a human has to say "yes" before action

The model does inference. **The Harness does everything else.** And "everything else" is where long-running agents succeed or fail.

---

## 2 · Three eras of Agent Engineering

AI agent design has evolved in three distinct phases:

| Era | Focus | Typical artefact |
|---|---|---|
| 2022–2024 | **Prompt Engineering** | A cleverly worded instruction |
| 2025 | **Context Engineering** | A well-assembled conversation snapshot |
| 2026 | **Harness Engineering** | A complete control system |

Prompt Engineering asked *"what should I say to the model?"*.

Context Engineering asked *"what should the model see right now?"*.

Harness Engineering asks *"what supports, constraints, and feedback loops should surround the model for long-running, high-stakes work?"*.

Harness covers: **Guards · Routing · Orchestration · Eval · Tools · RAG · Memory · System Prompt · State**.

---

## 3 · Four field observations driving the paradigm

These are observations from teams running agents in production over the past two years:

### 3.1 The ceiling is outside the model

The same model, wrapped in different Harnesses, produces dramatically different results on the same benchmark. Public case: a well-known agent framework upgraded its Harness and jumped from **52.8%** to **66.5%** on a canonical benchmark — leaderboard rank from **30+** to **top 5**. The model weights didn't change. The *environment around the model* did.

### 3.2 Safety is four-layered, not one

A real Harness has:

1. **Permission boundaries** — ACL: "this agent may call tool X but not tool Y"
2. **Sandbox isolation** — file and shell ops happen in a container or scoped directory, not on the host
3. **Operational audit** — every LLM call, tool call, approval decision, and key read is logged
4. **Human approval nodes** — for destructive or sensitive ops, a human has to click "approve" before the agent proceeds

Skipping any one of these is how agents end up deleting production tables or leaking secrets.

### 3.3 AI cannot reliably evaluate itself

An LLM grading its own output is systematically optimistic. This isn't a bug; it's the same self-confirmation bias humans exhibit when marking their own homework.

A serious Harness includes **independent evaluation** — a separate QA agent (or a classical rule-based validator) grading the primary agent's outputs from outside its context.

### 3.4 Optimising the "shell" beats waiting for the next model

For the next 12-18 months, investing in a better Harness has a higher ROI than waiting for a smarter model. This isn't about lowering expectations for AI — it's about making AI trustworthy enough to let it handle real work.

---

## 4 · The six layers of a Harness

Any production-grade Harness can be decomposed into six layers. SenHarness uses this decomposition as its core architectural commitment.

```
┌─────────────────────────────────────────────────────────────┐
│ L6 · Constraints & Recovery                                  │
│    what can/can't be done · output validation                │
│    retry · downgrade · rollback                              │
├─────────────────────────────────────────────────────────────┤
│ L5 · Evaluation & Observability                              │
│    output acceptance · env verification                      │
│    automated tests · logs · root-cause analysis              │
├─────────────────────────────────────────────────────────────┤
│ L4 · Memory & State                                          │
│    task state · intermediate results                         │
│    long-term memory · user preferences                       │
├─────────────────────────────────────────────────────────────┤
│ L3 · Execution Orchestration                                 │
│    understand goal → decide if info sufficient →             │
│    gather missing info → analyse → generate →                │
│    check → fix/retry                                         │
├─────────────────────────────────────────────────────────────┤
│ L2 · Tool System                                             │
│    which tools are available · when to call them             │
│    how results flow back (text predictor → actor)            │
├─────────────────────────────────────────────────────────────┤
│ L1 · Context Governance                                      │
│    define role / goal / success criteria                     │
│    slice information · structured layers                     │
│    (rules / task / state / evidence kept separate)           │
└─────────────────────────────────────────────────────────────┘
                         │
                         ▼
                    The LLM model
```

**Every feature in SenHarness maps to one of these layers.** The [architecture guide](architecture.md) is organised by this L1-L6 ordering precisely so that reviewers, contributors, and operators all share the same mental model.

---

## 5 · How SenHarness implements each layer

| Layer | SenHarness surface |
|---|---|
| **L1 · Context Governance** | `persona_md` + workspace-scoped memory fragments + system-prompt assembly + (V2) repo context injection (auto-load `AGENTS.md` / `README` / project docs) |
| **L2 · Tool System** | Built-in toolset · `BuiltinTool` registry · `shields.tool_guard` per-agent ACL · `HarnessPluginHost` 11-hook middleware · (V2) MCP servers + toolbox bindings |
| **L3 · Execution Orchestration** | `pydantic-ai` agent loop · `IterationBudget` · sub-agent capability (delegation) · visual flow engine · (V2) planning + verification loop |
| **L4 · Memory & State** | `memories` table + pgvector · session checkpoints · attachments · (V2) four-layer memory: semantic (MEMORY.md / USER.md) · episodic (session_search) · procedural (skill packs) · user modelling (SOUL.md) |
| **L5 · Evaluation & Observability** | Logfire · OpenTelemetry · Langfuse · Sentry · audit events · usage metrics · (V2) independent QA agent · trace replay UI · Prometheus |
| **L6 · Constraints & Recovery** | `shields` guardrails (PII · injection · secrets · blocked keywords) · budget tracking · autonomy levels (L1/L2/L3) · HITL approval queue · sandbox hardening · (V2) stuck-loop detection · tool error recovery · tool orphan repair · adaptive reasoning |

---

## 6 · Multi-tenant Harness — why it matters

Most Harness implementations we've seen are **single-user** — a CLI or a local app for one developer. That works for solo projects but breaks immediately at the enterprise level:

- Memory must be isolated per workspace (Tenant A's preferences can't bleed into Tenant B)
- Audit must be scoped per workspace (compliance auditors need "show me everything Tenant X did this month")
- Budget must be enforced per workspace (Tenant A's token splurge can't starve Tenant B)
- Approval routing must respect workspace departments and roles
- Skills, knowledge, and models must be shareable within a workspace without leaking across

SenHarness is **multi-tenant on day one**. Every model has a `workspace_id`. The same deployment runs unchanged from "one small team" to "thousands of SaaS tenants".

- **Self-hosted single-company**: one workspace (or a few, split by department/BU), `docker compose up`, done.
- **Multi-tenant SaaS** (future — *SenHarness Cloud*): one database, N workspaces, physical row-level isolation via `workspace_id` + platform-admin oversight.

No rewrite when the company grows from 1 tenant to 1000.

---

## 7 · Pluggable Agent Runtime — the open alternative

The model of `AgentBackend` as a Python protocol is deliberate. Most agent platforms bundle the runtime tightly:

- Dify is glued to its workflow engine
- Platforms that wrap one specific agent framework can't host another

SenHarness goes the other way. `AgentBackend` is a 4-method protocol (`run`, `cancel`, `capabilities`, `backend_kind`). Anything that can implement it is a valid runtime:

- The bundled `NativeBackend` (pydantic-ai powered, opinionated, fast)
- An OpenClaw remote worker (via the gateway + long-poll)
- Your own framework (50 lines of Python — see [adding-features.md#agent-runtime-adapter](adding-features.md#agent-runtime-adapter))

When a new agent framework appears in 2027, we don't need to fork; we write a new adapter file. **New runtimes are configuration, not code rewrites.**

---

## 8 · The long view — will Harness thin out over time?

Models are getting better. Context windows are growing. Long-horizon reasoning is improving. Memory is being baked into model weights. Tool use is becoming native.

Does that mean Harness is temporary? That the model will grow its own hands and feet?

**Partially yes.** Every generation of models absorbs some Harness capability into the model itself. Tool calls, context management, feedback loops, memory — each gets internalised. Harness layers will get thinner.

**But not all of them.** One thing the model will **never** generate by itself: **the destination**. Where to go. What matters. What *"success"* looks like for this particular company, this particular role, this particular moment.

Those questions — of direction, meaning, and value — are permanently human responsibility.

The Harness is where *we* shape the model into an agent of *our* purpose. As long as that matters, Harness Engineering will matter.

---

## 9 · Further reading

- [architecture.md](architecture.md) — SenHarness architecture organised by L1-L6
- [adding-features.md](adding-features.md) — writing custom adapters, tools, channels, connectors
- [quickstart.md](quickstart.md) — 10-minute single-company setup
- [deployment.md](deployment.md) — production hardening, sandbox choices, keyring providers

---

*This whitepaper is living documentation — it will evolve as the Harness Engineering paradigm and SenHarness itself mature. Feedback and contributions welcome via GitHub issues and PRs.*
