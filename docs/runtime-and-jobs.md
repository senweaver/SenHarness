# Runtime, Sessions, Memory, Jobs

Reference for the live agent runtime: how a chat turn flows through the
kernel, how sub-agents stay observable, how sessions/memory keep state
across turns, and how background jobs (judge, curator, verifier, evolver,
flow, retention) feed back into the runtime.

```
HTTP WS / channel / flow
        ↓
RunRequest → AgentBackend.run → RunEvent frames
        │
        ├─ inflight_runs spine (heartbeat, recovery, force-recycle)
        ├─ subagent_runs spine (reliability guards, batch fan-out)
        ├─ session messages + artifacts (capture, judge, replay)
        ├─ memory layers L1–L4 + cache-aware writes
        ├─ provider cache annotations (Anthropic / OpenRouter)
        └─ reflection injection (periodic + GAPA)
                ↓
        ARQ jobs (judge · curator · verifier · evolver · retention · insights · job_runs)
```

---

## Inflight runs (run lifecycle spine)

[`backend/app/db/models/inflight_run.py`](../backend/app/db/models/inflight_run.py)
+ [`backend/app/services/inflight_run.py`](../backend/app/services/inflight_run.py).
Durable record of every live agent run, with recovery hooks so a crash /
restart / hung provider can be detected and surfaced to the user.

State machine:

```
RUNNING ──► COMPLETED          (loop returned)
        ├─► CANCELLED          (WS cancel / asyncio.CancelledError / force-recycle)
        ├─► FAILED             (loop raised)
        └─► LOST                (recovery sweep)
```

`PAUSED` is reserved for a future HITL path. Terminal states are sticky
— a second `transition()` against an already-terminal row is a no-op.

`pid_token = host:pid:start_seconds` uniquely identifies the owning
process. The trailing `start_seconds` defeats PID reuse: a fresh process
on the same host cannot share a start time with its predecessor.

### Recovery sweeps

| Sweep                | Cron                                      | Trigger                                       | Outcome                                                       |
|----------------------|-------------------------------------------|-----------------------------------------------|---------------------------------------------------------------|
| `recover_inflight_runs` | Startup, FastAPI lifespan              | RUNNING row whose `pid_token` ≠ current process | `LOST`, `error_kind=backend_restart`, audit + notify           |
| `reap_stale_inflight_runs` | Every 5 min, minute `{4,14,24,34,44,54}` | RUNNING row, `last_seen_at < now − 15 min`    | `LOST`, `error_kind=heartbeat_timeout`, audit + notify         |

The cron decodes `pid_token`. If host matches AND `os.kill(pid, 0)`
succeeds → `spared_alive` (worker is slow, not dead; revisit next tick).
Otherwise → flip to `LOST`. The 15-minute cutoff sits above the longest
expected interactive turn so a slow run isn't reaped mid-flight.

### Heartbeat write throttling

Both the WS handler and the channel/flow runner call `update_last_seen()`
after every emitted `RunEvent` **except** `DELTA`. Per-token deltas
would turn a 1000-token reply into a 1000-write storm; the milestone
events (`TOOL_CALL` / `TOOL_RESULT` / `USAGE` / `THINKING` /
`APPROVAL_UPDATE` / `FINAL`) keep the row warm.

### WebSocket reconnect surface

On `WS /api/v1/sessions/ws/{session_id}` accept, the handler calls
`inflight_run.list_lost_for_session(...)`. If non-empty, it sends one
`system` frame so the frontend can offer `/retry`:

```json
{"type": "system", "data": {"kind": "lost_runs", "count": 2, "run_ids": ["…"], "session_id": "…", "message": "Previous run(s) were interrupted …"}}
```

The frame is fire-and-forget; the spine rows are not mutated on read so
the prompt survives a tab refresh.

### Checkpoints v2 (lineage-aware GC)

`session_checkpoints` carries `parent_checkpoint_id` (self-FK, `ON DELETE
SET NULL`) for fork lineage and `pruned_at` (nullable) for GC. The
`gc_old_checkpoints` cron runs daily at 04:45 UTC, selects rows
`pruned_at IS NULL AND created_at < now - 30d`, empties `snapshot_json`,
and stamps `pruned_at` — metadata + lineage survive, the heavy payload
is gone.

Audit: `inflight_run.registered`, `inflight_run.state_transitioned`,
`inflight_run.recovered_lost`, `inflight_run.timed_out_to_lost`,
`inflight_run.transition_failed`, `checkpoint.gc_pruned`.

Notification `inflight_run.lost_detected`: IN_APP only, WARN urgency,
cooldown 0 (each loss is independent), audience = actor.

---

## Runtime Console (M4.1)

Workspace-admin / platform-admin page at `/settings/system/runtime`.
Surfaces the live `inflight_runs` spine + one force-recycle action. No
schema changes.

State buckets projected for UI:

| Bucket    | Persisted `(state, error_kind)`                            | Color  |
|-----------|------------------------------------------------------------|--------|
| `running` | `RUNNING`                                                  | Green  |
| `paused`  | `PAUSED`                                                   | Amber  |
| `lost`    | `LOST` + non-heartbeat `error_kind` (e.g. `backend_restart`) | Gray   |
| `zombie`  | `LOST` + `error_kind=heartbeat_timeout`                    | Red    |
| `killed`  | `CANCELLED` (typically `error_kind=admin_force_recycle`)   | Black  |

`COMPLETED` / `FAILED` rows never reach the console. Listing surfaces
RUNNING + PAUSED unconditionally, plus `LOST` / `CANCELLED` rows with
`finished_at` in the last 30 min (`CONSOLE_RECENT_TERMINAL_WINDOW_SECONDS`).

Endpoints:

```
GET  /api/v1/admin/runtime/inflight-runs
GET  /api/v1/admin/runtime/stats
POST /api/v1/admin/runtime/inflight-runs/{run_id}/force-recycle
```

Auth: workspace owner / admin OR `identity.platform_role == platform_admin`.
Listing is workspace-scoped (no cross-workspace view in M4.1).
Rate buckets `runtime_console_read 60/60s`, `runtime_console_stats 30/60s`.

**Force recycle** triggers in order:

1. `backend.cancel(run_id)` — best-effort kernel cancel (Native flips
   the asyncio task; remote backends tell adapter to abort). We don't
   wait for the cancel to settle.
2. Transition row to `CANCELLED` + `error_kind=admin_force_recycle`.
   Idempotent.
3. Audit `inflight_run.force_recycled` with `previous_state` +
   `cancel_dispatched`.
4. Notification `inflight_run.force_recycled` to actor (the admin who
   clicked), cooldown 0.

Zombie / killed rows can't be recycled (button disabled). Frontend
polls every 5 s via TanStack Query. Pub/sub was considered but
rejected — `inflight_runs` is single source of truth, adding pub/sub
would mirror state across writers and add a SPOF.

---

## Sub-agent reliability (M2.5.1)

Roadmap principle 6: **a sub-agent must not silently hang**. Every child
run is registered in a durable spine ([`subagent_runs`](../backend/app/db/models/subagent_run.py)),
beats every 30 s, and is reaped within 60 s of the heartbeat going dark.

Four guards on one table:

1. **Heartbeat lifecycle** — capability hook in
   [`app/agents/harness/subagents.py`](../backend/app/agents/harness/subagents.py)
   registers a row at spawn and bumps `last_heartbeat_at` every 30 s.
2. **Zombie reaper** — `reap_zombies` ARQ cron at second 0 flips
   RUNNING rows whose heartbeat is > 5 min old to ZOMBIE (max 200 stale
   rows per tick).
3. **Retry budget** — `consume_retry_budget` enforces `retry_count <=
   retry_budget` (default 3). Exhaustion raises `RetryBudgetExhausted`
   (HTTP 409). The reaper refunds 1 budget when `retry_count > 0`.
4. **Hallucination gate** — child's final output is graded by an aux
   LLM; below threshold (default 0.5) parks in HALLUCINATION_REVIEW
   with a pending Approval (TTL 1 day).

State machine:

```
PENDING ─► RUNNING ─► COMPLETED
              │
              ├─► HALLUCINATION_REVIEW ─► COMPLETED (admin approved)
              │                       └─► KILLED   (admin rejected / TTL expired)
              ├─► ZOMBIE   (reaper sweep)
              ├─► KILLED   (admin / parent cancel)
              └─► FAILED   (child raised in-loop)
```

`transition_state` is idempotent on terminal states (COMPLETED, ZOMBIE,
KILLED, FAILED) so the reconnect path can call it safely.

Tunables (module constants in `app.services.subagent_run`):

| Constant                                | Default | Notes                           |
|-----------------------------------------|---------|---------------------------------|
| `HEARTBEAT_INTERVAL_SECONDS`            | 30      | Capability hook cadence         |
| `HEARTBEAT_DEAD_SECONDS`                | 300     | Reaper threshold                |
| `DEFAULT_HALLUCINATION_THRESHOLD`       | 0.5     | Gate boundary                   |
| `HALLUCINATION_APPROVAL_TTL`            | 1 day   | Roadmap TTL strategy            |
| `HALLUCINATION_BREAKER_TRIP_AT`         | 3       | Strikes inside the window       |
| `HALLUCINATION_BREAKER_WINDOW_SECONDS`  | 300     | 5-min sliding window            |
| `HALLUCINATION_BREAKER_RECOVER_SECONDS` | 1800    | 30-min cool-down                |

The hallucination breaker (`subagent:hallucination:fail:<workspace_id>`)
is **fail-open** — a downed aux LLM completes children with
`subagent.hallucination_passed` (reason `breaker_open`) rather than
blocking every child on review.

Gate prompt (`_PROMPT_SYSTEM`) asks the aux for strict JSON
`{score: float in [0,1], rationale: short, has_evidence: bool}`. User
prompt is hard-trimmed 4 KB head + tail. Unparseable shapes degrade to
fail-open 0.5 with no breaker bump.

Approval routing: `_apply_subagent_hallucination_review` in
`approval_dispatch.py`. Approve → COMPLETED + `subagent.hallucination_approved`;
reject (or TTL) → KILLED + `subagent.hallucination_rejected`.

Audit keys: `subagent.run_registered`, `subagent.state_transitioned`,
`subagent.heartbeat_lost`, `subagent.zombie_detected` (notification),
`subagent.zombie_reaped` (reaper transition),
`subagent.retry_budget_exhausted`, `subagent.hallucination_passed`,
`subagent.hallucination_review_required`,
`subagent.hallucination_rejected`, `subagent.hallucination_approved`.

### Batch fan-out (M2.5.6)

`delegate_batch` lets a parent agent fan out N focused workers in
parallel. Registered as a workspace-scoped builtin tool. Args:

```python
{
  "tasks": [{
      "task_id": "<unique 1-80 chars>",
      "prompt": "<…>",
      "target_agent_id": "<UUID in this workspace>",
      "timeout_seconds": 300,        # 1-600
      "inherit_skills": true,         # informational today
      "inherit_memory": false,
  }],
  "max_concurrent": 5                 # optional, 1-10
}
```

Per-task status values: `completed`, `halluc_review`, `failed`,
`timeout`, `cancelled`, `rejected`. Result envelope carries
`{total, completed, failed, timed_out, halluc_review, rejected,
duration_ms, serial_fallback, max_concurrent_used, results}`.

Workspace + platform config under
`workspace.home_config_json["subagent"]`:

| Field               | Default | Range  | Meaning                                                              |
|---------------------|---------|--------|----------------------------------------------------------------------|
| `batch_enabled`     | `true`  | bool   | When false, every batch falls back to serial.                        |
| `max_batch_size`    | `20`    | 1-100  | Hard ceiling per `delegate_batch` call. `1` forces serial.           |
| `max_concurrent`    | `5`     | 1-20   | Per-parent concurrency cap. Caller `max_concurrent` is clamped.      |
| `max_nesting_depth` | `3`     | 1-10   | Reject batches whose `spawn_depth` would meet or exceed this depth.  |

Invariants:

1. Per-child spine rows share `parent_run_id`; reaper / retry / gate
   index off the same column.
2. One child failing never blocks siblings — `asyncio.gather(return_exceptions=True)` plus
   per-child try/except → uncaught crashes become `status='failed'`.
3. Gate runs per child independently; workspace breaker open → fail-open
   per child, no global short-circuit.
4. Concurrency cap clamped at workspace policy
   (`min(caller_request, workspace.max_concurrent)`).
5. Nesting depth fails fast — `spawn_depth >= max_nesting_depth` →
   `rejected/nesting_depth_exceeded` per task, no spine rows written.
6. Over-quota tasks → `rejected/batch_size_exceeded` slots so the parent
   learns to size future batches correctly.

Serial fallback triggers: `batch_enabled=false` (reason `batch_disabled`),
`max_batch_size=1` (`max_batch_size_one`), `len(tasks)==1` (`single_task`).
Serial fallback uses every per-child spine + gate hook — only the
`asyncio.Semaphore` width drops to 1.

Audit: `subagent.batch_started`, `subagent.batch_completed`,
`subagent.batch_serial_fallback`, `subagent.nesting_depth_exceeded`.

---

## Session goals (M0.1)

A session goal lock pins a chat session to an explicit "north star".
Every assistant message that lands afterwards triggers an ARQ job
(`score_message_alignment`) which scores against the goal text + success
criteria.

Score bands (per [`backend/app/services/session_goal.py`](../backend/app/services/session_goal.py)):

| Band   | Score range                  | Colour |
|--------|------------------------------|--------|
| High   | `>= threshold + 0.2`         | green  |
| Medium | `threshold .. threshold + 0.2` | amber |
| Low    | `< threshold`                | red    |

Slash commands (intercepted before the agent loop — LLM never sees the
meta command, no tokens spent):

```text
/goal                — show active goal
/goal <text>         — lock with default threshold
/goal unlock         — unlock active goal
```

Routes (under `/api/v1/sessions/{session_id}`, identity must be active
workspace member; reads 120/min, writes 20/min, realign 10/min):

| Method | Path                              | Description                                |
|--------|-----------------------------------|--------------------------------------------|
| POST   | `/goals`                          | Lock a goal. Refuses while another active. |
| PATCH  | `/goals/{goal_id}`                | Update threshold / criteria / text.        |
| POST   | `/goals/{goal_id}/unlock`         | Unlock (idempotent).                       |
| GET    | `/goals?only_active=true`         | List goals for the session.                |
| GET    | `/alignment`                      | Per-message score history.                 |
| POST   | `/messages/{message_id}/realign`  | Re-queue a fresh score.                    |

Cross-workspace probes return `404 session_goal.not_found` rather than
leak row existence. Three consecutive aux failures in 60 s trip a
workspace breaker (60 s recovery). On no aux model: degrade to neutral
0.5 + audit row.

Aux resolution: `aux_model_goal_alignment` →
`aux_model_default` → workspace's first enabled chat model.

---

## Session artifacts (M0.2)

[`backend/app/services/session_artifact.py`](../backend/app/services/session_artifact.py).
Immutable, structured record of one agent run — single source of truth
for downstream judge / curator / evolver pipelines.

Schema (model in [`backend/app/db/models/session_artifact.py`](../backend/app/db/models/session_artifact.py)):

```python
class SessionArtifact:
    workspace_id, session_id, agent_id, identity_id
    run_id (unique)              # idempotency anchor
    user_text_hash               # SHA-256 of NFC-normalised, stripped user text
    turns_json: list[ArtifactTurn]
    injected_skill_pack_ids: list[str]
    invoked_tools: list[str]
    iteration_count: int
    final_outcome: success | error | partial | cancelled
    error_kind: str | None
    judge_score: float | None       # judge writes back here
    goal_alignment_avg: float | None
    finished_at: datetime
    # + UuidPk + Timestamp + SoftDelete + WorkspaceScoped
```

`turns_json[*]` carries `{role, text, tool_calls, tool_results,
thinking, iteration, message_id, timestamp}`.

### Invariants

1. **Idempotency** — `run_id` unique; capture is select-first with
   ON CONFLICT race recovery. Re-entry is a no-op returning the existing
   row.
2. **No raw user text** — only the SHA-256 digest survives on this row.
   Raw turn body lives in `messages`.
3. **Lineage** — `turns_json[*].message_id` is canonical pointer back
   to `messages.id`. Compression/archival must preserve.
4. **Workspace scoping** — every read filters on `workspace_id`. Cross-
   tenant requests → `404 session_artifact.not_found`.
5. **Fail-open capture** — capture errors record
   `artifact.capture_failed` and return `None`. The chat turn never
   breaks because of a downstream pipeline outage.

Final outcome heuristic:

| Outcome     | Trigger                                                                                  |
|-------------|------------------------------------------------------------------------------------------|
| `success`   | `RunEventKind.FINAL` arrived, no exception.                                              |
| `cancelled` | WS turn task caught `asyncio.CancelledError`.                                            |
| `partial`   | Exception / `RunEventKind.ERROR` **and** at least one assistant `delta`/`tool_call` was sent. |
| `error`     | Pure failure — no assistant output reached the client.                                   |

REST surface (all read-only — capture is server-internal):

| Method | Path                                                    | RBAC     | Rate                          |
|--------|---------------------------------------------------------|----------|-------------------------------|
| GET    | `/api/v1/sessions/{session_id}/artifacts`               | member   | `session_artifact_read 120/min` |
| GET    | `/api/v1/artifacts/{artifact_id}`                       | member   | same                          |
| GET    | `/api/v1/workspaces/{workspace_id}/artifacts/recent`    | admin    | `artifact_admin_read 60/min`  |

---

## Session search summarisation (M2.5.8)

Wraps the L2 episodic-memory `session_search` tool with an aux-LLM
distillation pass. Agent gets `{summary, bullet_points,
evidence_message_ids}`; the prompt cache stays valid for the rest of the
turn because the summary is a transient tool result that never enters
`message_history`.

Tool contract ([`backend/app/agents/tools/session_search.py`](../backend/app/agents/tools/session_search.py)):

```python
class SessionSearchArgs:
    query: str            # 1-500 chars
    limit: int = 10       # 1-50
    role: str | None
    session_id: str | None
    summarize: bool = True
    summary_max_chars: int = 800   # 100-4000
```

Returns one of:

| Path                                              | Shape                                                                                  |
|---------------------------------------------------|----------------------------------------------------------------------------------------|
| `summarize=False` or zero hits                    | `{query, hits, summarized: False}`                                                     |
| Happy path                                        | `{query, summary, bullet_points, evidence_message_ids, based_on_count, raw_results, summarized: True}` |
| Fallback (breaker / aux failure / rate limit)     | `{query, hits, summarized: False, fallback_reason}` (`breaker_open` / `aux_failure` / `rate_limited`) |

Aux resolution chain: `workspace.aux_model_summarize` →
`workspace.aux_model_judge` → `workspace.aux_model_default` → first
enabled chat model. Per-workspace budget `summarize_rate_per_minute`
(default 30/min).

**Evidence-id contract.** The aux LLM is told `evidence_message_ids`
MUST appear verbatim in the search results. The tool filters proposed
ids against `{hit.message_id for hit in raw_hits}` — rejected ids
trigger `summarize.evidence_filtered`. Defence against fabricated links.

Breaker `summarize:fail:<workspace>` is independent from judge, verifier,
and evolver buckets.

Audit: `summarize.invoked` (carries `query_hash` SHA-256 prefix, not
raw query), `summarize.fallback`, `summarize.evidence_filtered`,
`summarize.breaker_tripped`.

---

## Cross-platform session continuity (M3.6)

Opt-in (default `cross_platform_enabled=False`). Lets a single user
continue the same conversation across multiple IM channels.

Concepts:

* **Logical thread** — `(workspace, identity, agent)` conversation arc;
  one canonical `primary_session_id`. The agent's history view is the
  same regardless of which channel the inbound came from.
* **Thread-channel binding** — `(thread, channel, external_user_id)`
  edge. May be `is_paired=False` (created automatically on first inbound)
  or `is_paired=True` (user completed handshake on both sides).
* **Pairing** — out-of-band 6-digit handshake stored in Redis only
  (10-min default TTL, burns on first redemption). Only way two distinct
  bindings collapse into the same thread.

Routing flow when enabled:

1. Inbound webhook → `dispatch_inbound` → `find_or_create_thread_for_inbound`.
2. Hit `(channel_id, external_user_id)` in `thread_channel_bindings` →
   reuse `thread.primary_session_id`.
3. Miss with identity known → fresh thread + session + `is_paired=False`
   binding. User can later finish pairing to merge.
4. Miss without identity → return `None`; caller falls back to legacy
   per-channel path. Common case where the platform user id has not yet
   been mapped to a SenHarness identity.

Pairing handshake:

```
[Web UI A] --initiate--> [Redis: 6-digit code, 10 min TTL] <--consume-- [Web UI B]
```

Consumer must match the target's `(channel_id, external_user_id)` —
mismatch → `thread.pairing_target_mismatch` and **code is not burned**
(legitimate user can still redeem). Pairing codes are 6-digit numeric
from `secrets.randbelow`; the `/pair/consume` 10/60s rate limit is the
load-bearing brute-force guard.

Routes:

| Method | Path                                  | Rate bucket                 | Notes                                        |
|--------|---------------------------------------|-----------------------------|----------------------------------------------|
| GET    | `/threads`                            | `threads_list 60/60s`       | List threads owned by caller.                |
| GET    | `/threads/{id}`                       | same                        | Thread + bindings hydrated.                  |
| GET    | `/threads/{id}/sessions/active`       | same                        | Resolve canonical session for chat surface.  |
| POST   | `/threads/{id}/label`                 | `threads_relabel 10/60s`    | Set / clear user label.                      |
| POST   | `/threads/pair/initiate`              | `threads_pair_init 5/300s`  | Issue pairing code.                          |
| POST   | `/threads/pair/consume`               | `threads_pair_consume 10/60s` | Redeem; merges threads when bindings differ. |
| GET    | `/threads/{id}/bindings`              | `threads_list 60/60s`       | List bound channels.                         |
| DELETE | `/threads/{id}/bindings/{binding_id}` | `threads_binding_delete 10/60s` | Soft-delete one binding.                |

All routes are identity-scoped (service filters on both `workspace_id`
and `identity_id`). `external_user_id` values are SHA-256 hashed
(12-hex prefix) before being written into audit metadata.

Failure codes: `thread.cross_platform_disabled`,
`thread.pairing_code_invalid`, `thread.pairing_code_expired`,
`thread.pairing_target_mismatch`, `thread.pairing_source_missing`,
`thread.not_found`, `thread.binding_not_found`, `thread.session_missing`.

Audit: `thread.created`, `thread.binding_created`, `thread.binding_paired`,
`thread.binding_unbinded`, `thread.pairing_code_issued`,
`thread.pairing_code_consumed`, `thread.pairing_code_expired`,
`thread.merged`.

`logical_threads` + `thread_channel_bindings` are in M0.11 cascade list
(soft-delete + workspace + identity scoped).

---

## Cross-session insights (M4.5)

User-driven summarisation. User types `/insights [--days N]` in any
chat session; platform reads their own session artifacts in the active
workspace and surfaces 3–7 actionable observations as a regular
assistant markdown message.

Slash grammar:

```
/insights                  → default day window (workspace setting)
/insights --days N         → custom window, 1 ≤ N ≤ max_days (cap 90)
/insights --days=N         → equivalent
```

Malformed payloads fall back to default; the meta command never reaches
the LLM. The user sees `system.insights_queued` toast; markdown reply
arrives ~30 s later from ARQ worker.

Aux flow ([`backend/app/jobs/insights.py`](../backend/app/jobs/insights.py)):

1. WS handler enqueues `generate_insights` on ARQ with
   `(workspace_id, identity_id, return_session_id, days)`.
2. Job reads `session_artifacts` filtered by
   `(workspace_id, identity_id, finished_at >= now - days,
    final_outcome != 'cancelled')`. **Identity filter is the privacy
   gate** — only the caller's artifacts feed the aux prompt.
3. Aux resolved via SUMMARIZE chain
   (`InsightsSettings.aux_model` → `aux_model_summarize` →
   `aux_model_judge` → workspace default).
4. Response (`InsightsResult` of `InsightItem`) resolved against
   artifact lookup table — aux receives `artifact_id` references; runtime
   expands to clickable `session_id` links so private artifact ids never
   leak into chat.
5. Markdown persisted as one assistant `Message` with
   `metadata_json.kind = "cross_session_insights"`.

Breaker model: **shared evolver bucket** (`EVOLVER_BREAKER_BUCKET="evolver"`).
A sick aux model disables every cross-session signal at once instead of
degrading silently across surfaces. Open → degrade to deterministic
heuristic clusterer with `_degraded fallback (aux unavailable)_` banner.

Settings ([`InsightsSettings`](../backend/app/schemas/platform_settings/insights.py)):

| Field                       | Default | Range  | Notes                                                    |
|-----------------------------|---------|--------|----------------------------------------------------------|
| `enabled`                   | `True`  | bool   | Kill switch.                                             |
| `default_days`              | `30`    | 1-180  | Window when `--days` omitted.                            |
| `max_days`                  | `90`    | 1-180  | Caller cap (≥ default_days).                             |
| `aux_model`                 | `None`  | str    | Overrides `aux_model_summarize`.                         |
| `max_artifacts_per_summary` | `200`   | 10-500 | Hard cap on aux prompt input.                            |
| `max_items_per_summary`     | `7`     | 1-20   | Hard cap on rendered insights.                           |

Routes:

| Method | Route               | Rate bucket               | Purpose                                |
|--------|---------------------|---------------------------|----------------------------------------|
| POST   | `/insights/generate`| `insights_generate 3/300s`| Same path as slash; UI button surface. |
| GET    | `/insights/recent`  | `insights_recent 30/60s`  | Caller's last few runs (sidebar).      |

`/recent` reads audit log scoped to `actor_identity_id == caller` — the
privacy boundary can't be relaxed without touching the audit write site.

Audit: `insights.queued`, `insights.cross_session_summarized`,
`insights.aux_skipped`, `insights.failed_permanent`.

Does NOT touch: notification fan-out (user-pull only),
cross-workspace federation, automatic schedule (deliberately no cron),
or `messages.compressed_into_summary_id` (one layer up at artifact level).

---

## Memory: four layers

| Layer | Name              | What                                                       | Where                          | Reaches the Agent                        |
|-------|-------------------|------------------------------------------------------------|--------------------------------|------------------------------------------|
| L1    | Semantic profiles | Workspace MEMORY.md + identity USER.md + identity SOUL.md  | `memory_profiles` table        | Auto-injected at top of system prompt, capped per kind |
| L2    | Episodic          | Every message the workspace has recorded                   | `messages` + tsvector index    | `session_search` tool (on-demand)        |
| L3    | Procedural        | Skill packs                                                | `skill_packs` / `skill_files`  | pydantic-ai Skills loader (lazy)         |
| L4    | User modelling    | SOUL.md + 12 canonical dimensions                          | `memory_profiles` (kind=user_soul) | Auto-injected next to USER.md; writes go through approval |

### L1 semantic profiles

Three markdown documents, one row each in `memory_profiles`:

| Document      | Cap (chars) | Route                                    | Write RBAC                        |
|---------------|-------------|------------------------------------------|-----------------------------------|
| MEMORY.md     | 2200        | `GET / PUT /api/v1/memory-profiles/workspace` | workspace admin              |
| USER.md       | 1375        | `GET /api/v1/memory-profiles/me` + `PUT .../me/profile` | identity (or agent on user request) |
| SOUL.md       | (approval-gated, see L4) | `POST /api/v1/memory-profiles/me/soul/propose` | passive accumulation + approve |

### L2 episodic memory

Every message eligible for full-text search via
`to_tsvector('simple', coalesce(content_json->>'text', ''))` GIN index.
Workspace isolation enforced at SQL level
(`WHERE messages.workspace_id = :ws`). Use `plainto_tsquery('simple',
…)` so the LLM passes natural language.

`recall` (vector cosine) for "what do we know about X?" cross-scope
queries; `session_search` for "replay a prior conversation".

### L4 SOUL.md + 12 dimensions

`app.db.models.memory_profile.SOUL_DIMENSIONS`:
`communication_style`, `domain_expertise`, `tone_and_register`,
`goals_current`, `constraints`, `preferences_tools`,
`preferences_language`, `cadence`, `identity_signals`, `workflow`,
`avoid_list`, `history_summary`. Forks may add extra string keys.

**Approval-gated writes.** Direct PUT to `user_soul.content_md` is
refused — callers propose; identity (or admin) approves:

```
POST /api/v1/memory-profiles/me/soul/propose
{
  "proposed_content": "…",
  "proposed_dims": {"communication_style": "prefers bullet points"},
  "source_session_id": "…",
  "rationale": "inferred over last 14 sessions"
}

POST /api/v1/memory-profiles/me/soul/{proposal_id}/decide
{"decision": "approve"|"reject", "reason": "…"}
```

On approval, dims are merged into `soul_dims_json` (new fragments win
per key). Full decision history kept in `metadata_json.decisions` (last
50). Every propose/approve/reject writes audit.

### Injection order (system prompt)

[`app.agents.harness.memory.fetch_system_memory_fragment`](../backend/app/agents/harness/memory.py)
composes, in order:

1. `## WORKSPACE MEMORY` — MEMORY.md
2. `## USER PROFILE` — USER.md
3. `## USER SOUL` — SOUL.md
4. `### SOUL DIMENSIONS` — populated-only 12-dim bullet list
5. `## RECALLED NOTES` — top-k vector-recalled `memories` rows

Each section skipped when empty.

### Cache-aware writes (M0.7)

Default: every `memorize` (and, in M1, `skill.write`) defers to
`effective="next_session"` so the system prompt already in provider
cache stays valid until the next run boots fresh.

Lifecycle:

```
agent calls memorize(content="…", effective="next_session")
  └─> queue_immediate_or_pending → queue_pending_memory
                                  → audit pending_memory.queued
                                  → audit memory.deferred_to_next_session
                                  ── status PENDING in pending_memories

… end of run …
WS / channel/flow path: capture_run_artifact → enqueue_judge → _promote_pending_memories
```

Promote runs **after** judge enqueue so a slow promote can't delay
judge scheduling. Both wrapped in try/except — a downed memory
pipeline can't break the user-facing turn.

`effective="now"` workspace gate (default-deny):

```jsonc
// workspaces.home_config_json
{"memory": {
  "allow_immediate": true,           // default false
  "always_on_max_chars": 4000,
  "permitted_scopes": ["user", "assistant", "workspace"]
}}
```

Gate closed + `effective="now"` → structured rejection
`memory.immediate_not_permitted` (not a hard error — agent can react).

**Hard cap on always-on memory** applies per `(workspace, scope,
scope_id)` bucket. Over cap:

| Path                         | Outcome                                                                                                  |
|------------------------------|----------------------------------------------------------------------------------------------------------|
| `effective="now"`            | `MemoryHardCapExceeded` → `status="rejected"`, `code="memory.hard_cap_exceeded"`                         |
| `effective="next_session"`   | Pending row queues fine; promote step traps the cap → `SKIPPED` + `memory.hard_cap_blocked` audit        |

ARQ workspace sweep `pending_memory_workspace_sweep` at `minute={2, 32}`.
Picks PENDING rows older than 30 min; skips rows whose parent session is
recently active (last_message_at within 30 min) — sync hook owns those.
Three-strike permanent failures → `on_pending_memory_job_failed_permanent`.

Routes:

| Method | Route                                                | RBAC                            | Rate                                  |
|--------|------------------------------------------------------|---------------------------------|---------------------------------------|
| GET    | `/sessions/{id}/pending-memories`                    | session-scoped (workspace member) | `pending_memory_read 60/60s`        |
| POST   | `/sessions/{id}/pending-memories/{pid}/cancel`       | session owner / workspace admin   | `pending_memory_cancel 10/60s`       |
| GET    | `/workspaces/{id}/pending-memories/stats`            | workspace admin                  | `pending_memory_admin_read 30/60s`   |
| POST   | `/admin/pending-memories/promote-now`                | platform admin                   | `admin_pending_memory_trigger 5/300s`|

Audit: `pending_memory.queued`, `memory.deferred_to_next_session`,
`memory.applied_immediate`, `memory.immediate_not_permitted`,
`memory.promoted_from_pending`, `memory.promotion_completed`,
`memory.promotion_failed`, `memory.hard_cap_blocked`,
`pending_memory.cancelled`, `admin.pending_memory.trigger`.

---

## Provider response caching (M2.5.9)

SenHarness annotates outbound LLM payloads with provider-native
prompt-cache markers when the upstream supports them, and adaptively
disables them per `(workspace, provider)` pair when the provider stops
honouring them.

Provider matrix:

| Provider          | Markers           | Wiring                                            |
|-------------------|-------------------|---------------------------------------------------|
| `anthropic`       | ✓                 | Native via pydantic-ai `anthropic_cache_*`. 4 breakpoints. |
| `openrouter`      | ✓ (passthrough)   | Intent stash on `model_settings`; per-message wiring deferred. |
| `openai`/`azure_openai`/`deepseek`/`google`/`xai`/`moonshot` | — | NoOp profile. |

Add a new provider: register kind in
[`PROVIDER_CACHE_PROFILES`](../backend/app/services/cache_control.py)
with `supports_cache_control=True` and `max_breakpoints` (Anthropic +
OpenRouter cap at 4 — exceed → upstream 400).

### Breakpoint selection

`annotate_cache_breakpoints` picks indices in order, stopping at
`max_breakpoints`:

1. First system message (system prompt — byte-stable across turns).
2. Any subsequent system message that follows immediately (multi-part
   system prompts emitted by the harness composer).
3. Earliest *long* user message (≥ 800 chars) — typically retrieved
   context.
4. Last user message — every turn rotates here, fresh marker per turn.

Marker lands on the **last content block** of the picked message.
Plain string `content` is widened to a one-element list on first
annotation so downstream serialisers see a uniform shape.

Annotation skipped when estimated prompt size below `min_prompt_tokens`
(default 1024). Estimate is `total_chars // 4` — no tokenizer
dependency.

TTL options: `5m` (default, Anthropic ephemeral) or `1h` (gates on
`extended-cache-ttl-2025-04-11` Anthropic beta header — operators must
enable explicitly because beta affects pricing).

Workspace override (`workspace.home_config_json`):

```jsonc
{"providers": {"cache_control": {
  "enabled": true,
  "min_prompt_tokens": 1024,
  "max_breakpoints": 4,
  "ttl": "5m",
  "adaptive_disable_threshold": 5,
  "adaptive_disable_duration_seconds": 60
}}}
```

### Adaptive disable

[`cache_adaptive`](../backend/app/services/cache_adaptive.py): when a
provider returns zero `cache_read_input_tokens` for
`adaptive_disable_threshold` consecutive turns (default 5), disables
annotation for that `(workspace, provider_kind)` pair for
`adaptive_disable_duration_seconds` (default 60 s).

Audit: `cache.annotated`, `cache.adaptive_skipped`, `cache.hit`,
`cache.miss_recorded`, `cache.adaptive_disabled`, `cache.adaptive_recovered`.
Notification `cache.adaptive_disabled` to `workspace_admins`, IN_APP,
1 h cooldown per pair. `requires_email=False`.

Degraded Redis fails open: predicate returns `False`, runner never
locks into permanent NoOp.

Interaction with M2.5.3 failover chain: each chain attempt re-resolves
the per-provider profile from scratch — Anthropic markers don't ride
into a DeepSeek attempt. Tracker keyed by `provider_kind`, not chain
position.

Interaction with M2.5.7 served-name pattern: stats key on
`provider_kind`, never on `served_model_name`. A workspace alias map
redirect (e.g. `ws-fast: openrouter → anthropic`) resets the miss
counter cleanly because the new attempt's stats land under a different
key.

---

## Evolver

The platform-builtin **evolver subagent** + the **workflow engine** turn
low-scoring runs into SkillPack proposals. Always proposals + Approval
rows; never mutates live skills directly.

### Subagent (M2.2)

[`backend/app/agents/builtin/evolver_agent.py`](../backend/app/agents/builtin/evolver_agent.py).
A single-shot pydantic-ai agent. ARQ workflow engine (M2.3) or admin
endpoint fires one invocation; the agent reasons about the batch, files
at most a handful of Approval rows, exits. The evolver **never** appears
in the workspace `agents` table — platform-shared definition.

Persona loaded once from
[`backend/app/agents/templates/evolver_persona.md`](../backend/app/agents/templates/evolver_persona.md)
(< 800 chars, cached in memory). Instructs the agent to:

1. Read first (`list_session_artifacts` → `read_skill_pack`).
2. Prefer small `propose_skill_patch` over full-document `edit`.
3. Always cite real `supporting_run_ids`.
4. Call `mark_skip` when the batch is healthy.

9 tools, all `available_for_kinds=("evolver",)` — runner skips
registration for non-evolver agents:

| Tool                          | Purpose                                                                |
|-------------------------------|------------------------------------------------------------------------|
| `list_session_artifacts`      | Recent low-scoring runs (read-only).                                   |
| `read_skill_pack`             | Pack metadata + ACTIVE content (truncated 8 KiB, read-only).           |
| `propose_skill_create`        | New SkillPack proposal.                                                |
| `propose_skill_patch`         | Targeted `old_text → new_text` edit.                                   |
| `propose_skill_edit`          | Full-document body replacement.                                        |
| `propose_skill_delete`        | Archive an existing pack (refuses pinned).                             |
| `propose_skill_write_file`    | Add / replace supplementary file.                                      |
| `propose_skill_remove_file`   | Remove supplementary file.                                             |
| `mark_skip`                   | Halt the loop with `evolver.marked_skip` audit.                        |

Hard outer timeout `EVOLVER_AGENT_TIMEOUT_SECONDS = 300`. Shared breaker
`evolver:fail:<workspace_id>` keyed off workspace_id.

Pre-flight raises:

* `EvolverDisabled` — workspace opted out (HTTP 409).
* `EvolverBreakerOpen` — breaker tripped (HTTP 503).
* `EvolverAuxModelMissing` — no aux model resolved (HTTP 412).

After run starts, every outcome (success / skip / timeout / internal
exception) returns a populated `EvolverInvokeResult` — caller never
sees an in-flight exception.

Aux resolution (first match wins):

1. `EvolverSettings.aux_model_evolver`.
2. `auxiliary_client.get_aux_model(task=SKILL_REVIEW)`.
3. `auxiliary_client.get_aux_model(task=JUDGE)`.
4. `EvolverAuxModelMissing`.

Admin endpoint:

```
POST /api/v1/admin/workspaces/{workspace_id}/evolver/invoke
  body: {"triggering_run_ids": ["…"]}
Auth: platform admin OR workspace owner/admin.
Rate: evolver_admin_invoke 3/300s.
```

Audit: `evolver.subagent_invoked`, `evolver.subagent_completed`,
`evolver.subagent_timeout`, `evolver.subagent_failed`,
`evolver.marked_skip`.

### Workflow engine (M2.3)

[`backend/app/services/evolver_workflow.py`](../backend/app/services/evolver_workflow.py)
+ [`backend/app/jobs/evolver.py`](../backend/app/jobs/evolver.py).
Daily driver: scans last 7 days of low-scoring `session_artifacts`, files
proposals via M2.7 verbs (`engine="workflow"`) or hands off to the
subagent (`engine="agent"`).

Five-stage pipeline (`engine="workflow"`):

```
drain → summarize → aggregate → evolve → publish
```

| Stage       | Side effects                                                                                                                          |
|-------------|---------------------------------------------------------------------------------------------------------------------------------------|
| `drain`     | Reads `session_artifacts` where `judge_score < 0` in last 7 days, hard-capped at 200 rows.                                            |
| `summarize` | One aux LLM call (task=`SKILL_REVIEW`, fall-through to `JUDGE`) producing a 600-char prose seed. Falls back to deterministic seed.    |
| `aggregate` | Buckets the drain by `error_kind` (preferred) or by dominant `invoked_tool` (fallback). Buckets with < 2 artifacts dropped.            |
| `evolve`    | One aux call per cluster (structured output `_SkillDraft`) drafts a SkillPack body; workflow calls `run_propose_skill_create` directly. |
| `publish`   | Writes `evolver.workflow_completed` audit; resets shared breaker when ≥ 1 proposal landed.                                            |

Workflow never mutates a live skill. M2.4 runs the verifier; M2.5
dispatches approvals to `skill_version.activate_version`.

Cron `evolver_workspace_sweep` at **04:30 UTC daily** — sits 30 min
after retention purge so connection-pool usage settles before per-workspace
aux-LLM calls fan out.

Manual trigger:

```
POST /api/v1/skills/evolve/trigger
Body: {"workspace_id": "…", "bypass_min_artifacts": false}
Auth: workspace admin (or platform admin). Rate: skills_evolve_trigger 2/300s.
```

`bypass_min_artifacts=true` skips the `min_artifacts_per_evolution` gate
(default 5). The workspace `enabled` flag is **not** bypassable.

Skip reasons: `evolver_disabled`, `breaker_open`, `insufficient_artifacts`.

Audit: `evolver.workflow_completed`, `evolver.workflow_skipped`,
`evolver.workflow_failed`, `evolver.workflow_failed_permanent`,
`evolver.manually_triggered`.

### Propose verbs (M2.1 + M2.7)

[`backend/app/agents/tools/skill_propose.py`](../backend/app/agents/tools/skill_propose.py).
The six tools (and one cronjob verb) the evolver calls. **Never raises**;
caller continues regardless of outcome.

| Verb                          | Resource type            | Default TTL | Side effect on success                                              |
|-------------------------------|--------------------------|-------------|---------------------------------------------------------------------|
| `propose_skill_create`        | `skill_pack_create`      | 14 d        | `DRAFT` pack + `PROPOSED` v1 + Approval.                            |
| `propose_skill_patch`         | `skill_pack_patch`       | 14 d        | Approval + new `PROPOSED` version with patched body.                |
| `propose_skill_edit`          | `skill_pack_edit`        | 14 d        | Same as patch but for full-document replacement.                    |
| `propose_skill_delete`        | `skill_pack_delete`      | 7 d         | Approval (no version row); applies via `ARCHIVED` on approval.      |
| `propose_skill_write_file`    | `skill_pack_write_file`  | 14 d        | Approval carrying new file content.                                 |
| `propose_skill_remove_file`   | `skill_pack_remove_file` | 7 d         | Approval pointing at existing file row.                             |
| `propose_cronjob_create`      | `flow_create`            | 7 d         | Approval; dispatch creates `Flow(enabled=False)` (admin flips on).  |

Success returns `{status: "proposed", kind, approval_id, pack_id,
version_id?, version_no?, content_hash?, expires_at}`.

Stable rejection codes:

| Code                                  | Meaning                                                              |
|---------------------------------------|----------------------------------------------------------------------|
| `evolver.disabled`                    | Workspace evolver disabled (default).                                |
| `evolver.breaker_tripped`             | Redis breaker open; back off after cooldown.                         |
| `evolver.rate_limited`                | Workspace burned its propose budget for the current minute.          |
| `evolver.slug_in_use`                 | Pack with requested slug already exists.                             |
| `evolver.slug_tombstoned`             | Slug previously tombstoned (M1.1 invariant).                         |
| `evolver.pack_not_found`              | `pack_id` not a member of this workspace.                            |
| `evolver.pack_tombstoned`             | Cannot edit / patch / delete / touch files on a tombstoned pack.     |
| `evolver.pack_pinned`                 | `propose_skill_delete` blocked: user must unpin first.               |
| `evolver.no_active_content`           | Pack has no body to patch (propose `create` or `edit` instead).      |
| `evolver.patch_conflict`              | `old_text` not found verbatim in current ACTIVE version.             |
| `evolver.duplicate_content_hash`      | Resulting body matches existing version row (no-op).                 |
| `evolver.duplicate_pending`           | Another pending Approval already exists for this pack + verb.        |
| `evolver.invalid_path`                | Relative path contains `..`, `//`, or leading/trailing `/`.          |
| `evolver.reserved_path`               | `SKILL.md` cannot be written or removed via file verbs.              |
| `evolver.file_not_found`              | Pack has no file at given relative path.                             |
| `evolver.internal_error`              | DB / unexpected exception; breaker counter advanced one strike.      |

Default settings (`EvolverSettings`, platform → workspace overlay):

```python
EvolverSettings(
    enabled=False,                               # opt-in
    engine="workflow",                           # workflow | agent
    publish_mode="approval_required",            # approval_required | auto_after_validation
    min_artifacts_per_evolution=5,
    auto_verifier=EvolverAutoVerifier(enabled=True, min_score_delta=0.05, min_replay_artifacts=3),
    approval_ttl_days=EvolverApprovalTtlDays(
        skill_pack_create=14, skill_pack_patch=14, skill_pack_edit=14,
        skill_pack_delete=7, skill_pack_write_file=14, skill_pack_remove_file=7,
    ),
    aux_model_evolver=None,
    evolver_breaker_strikes=5,
    evolver_breaker_window_seconds=300,
    evolver_rate_per_minute=10,
)
```

Breaker + rate budget (both in `app.jobs._breaker`):

* `evolver:fail:<workspace_id>` — failure counter; bumps only on
  internal exceptions, not validation rejections. Fails open on Redis
  errors.
* `evolver_propose:rate:<workspace_id>` — sliding 60-s window;
  default 10/min.

Cronjob propose (M2.8) has its own rate bucket
`cronjob_propose:rate:<workspace_id>` capped at 5/min so cronjob spam
can't burn the skill-propose budget. Shared evolver breaker.

Approval body shape (cronjob example):

```jsonc
{
  "kind": "flow_create",
  "name": "OKR daily readback",
  "schedule": "0 9 * * *",
  "schedule_kind": "cron",         // cron | interval | one_shot
  "schedule_meta": {"expression": "0 9 * * *", "tz": "UTC"},
  "prompt_template": "Read me my OKR for today.",
  "target_agent_id": "…",
  "delivery_channel_ids": [],
  "rationale": "user asked twice this week"
}
```

Two human gates for cronjobs:

1. **Approval** — admin reads proposal; catches wrong agent / channel /
   prompt / off-topic schedule.
2. **`enabled=False` on Flow row** — admin must open Flow UI and click
   "Enable" to start the cron; catches approved-but-not-yet-ready
   schedules.

Schedule shapes: `cron` (5-field UTC, validated via APScheduler
`CronTrigger.from_crontab`), `interval` (regex `^every (\d+)([smhd])$`),
`one_shot` (ISO 8601, must resolve into the future).

Audit: `evolver.proposed_skill_<verb>`, `evolver.propose_rejected`,
`evolver.breaker_tripped`, `evolver.proposed_cronjob`,
`evolver.cronjob_rejected`.

---

## Run quality judge (M0.3)

Scores every captured `session_artifact` on a 1 / 0 / -1 scale.

| Score | `session_artifacts.judge_score` | Meaning                                                     |
|-------|---------------------------------|-------------------------------------------------------------|
| `1`   | `1.0`                           | Run materially accomplished the user's request.             |
| `0`   | `0.0`                           | Genuine partial — some progress, missing pieces.            |
| `-1`  | `-1.0`                          | Run clearly failed: wrong answer, fabricated tool result, broken constraint. |
| NULL  | —                               | Not yet judged, or `final_outcome="cancelled"` (skipped).   |

`judge_verdicts` row carries full reasoning (`rationale`,
`process_notes_json`, `error_kind_hint`); the float on
`session_artifacts` is for fast list queries. Both updated atomically.

Lifecycle:

1. **Capture** — `session_artifact.capture_from_run_outcome` on every run.
2. **Enqueue** — non-cancelled artifacts get
   `enqueue("judge_session_artifact", artifact_id, _defer_by=5)`.
   Best-effort `judge.enqueue_failed` audit on Redis hiccup.
3. **Judge** ([`backend/app/jobs/judge.py`](../backend/app/jobs/judge.py))
   resolves workspace aux, renders prompt
   ([`judge_run.md`](../backend/app/agents/templates/judge_run.md))
   plus trimmed `turns_json`, calls LLM with `JudgeVerdict` schema.
4. **Persist** — `services/judge.persist_verdict` atomic upsert.
   Re-judge replaces row in-place; audit row keeps history.
5. **Sweep** — hourly `judge_periodic_sweep` enqueues artifacts
   finished ≥ 5 min ago, still missing a score, breaker not open.

Breaker + rate (workspace override on `home_config_json["aux"]`):

| Tunable                            | Default | Override key                       |
|------------------------------------|---------|------------------------------------|
| Failure strikes (trip breaker)     | 5       | `judge_fail_strikes`               |
| Failure window                     | 5 min   | `judge_fail_window_seconds`        |
| Recovery time after trip           | 1 h     | `judge_breaker_recover_seconds`    |
| Sliding-window rate budget         | 60/min  | `judge_rate_per_minute`            |
| Max chars of trace fed to aux      | 12000   | `judge_turns_serialized_chars`     |
| Max chars of user prompt header    | 800     | `judge_prompt_max_chars`           |

Breaker open → verdict forced `score=0`, `degraded=True`,
`judged_by_model=NULL` + `judge.degraded` audit. **Curator must filter
`degraded=True` rows out of training data.**

Rate budget exhausted → ARQ `Retry(defer=20s)` so retry budget is
preserved (pure pressure shouldn't trip failure breaker).

Re-judge: `POST /api/v1/artifacts/{artifact_id}/rejudge` (workspace
admin, `artifact_rejudge 5/60s`). Deletes verdict, NULLs `judge_score`,
re-enqueues. Audit `judge.rejudge_requested`.

Audit: `judge.enqueued` (reserved), `judge.enqueue_failed`,
`judge.completed`, `judge.degraded`, `judge.skipped_cancelled`,
`judge.skipped_already`, `judge.rejudge_requested`, `job.failed_permanent`.

---

## Reflection hook (M0.4 + M0.5)

The native runner periodically asks the agent to "step back and look at
what it's doing" without showing the prompt to the user and without
polluting persisted message history. Two triggers share one decision:

| Trigger                       | Kind         | Default cadence       |
|-------------------------------|--------------|-----------------------|
| Periodic graph iterations     | `periodic`   | every **8** iterations |
| Tool-call accumulation (GAPA) | `tool_call`  | every **15** tool calls |

Whichever trips first wins; the other is suppressed for the same
iteration so a single graph step never injects two reflections.

### Cache safety

Reflection prompts inserted as fresh `SystemPromptPart` at head of
`ModelRequestNode.request.parts`, immediately before the runner streams
that node. Three properties keep provider caches happy:

1. Mutation only lives inside the current `Agent.iter()` call. The
   runner never persists `state.message_history` — only `RunEvent`
   deltas reach WS + `messages` table.
2. Next user turn calls `_rehydrate_history` which reads DB-shaped
   rows; ephemeral system part is gone, prefix is byte-identical.
3. Within a single `Agent.iter()`, the *next* model call after injection
   is a guaranteed cache miss. Trade-off is intentional — policy keeps
   reflections rare (default ≥ 8 iters apart).

Locked by
[`backend/tests/unit/agents/test_reflection_no_cache_break.py`](../backend/tests/unit/agents/test_reflection_no_cache_break.py).

### Configuration

Precedence: `agent.policy.reflection.<field>` >
`workspace.home_config_json.reflection.<field>` > defaults, **except
`enabled`** — `enabled` is the AND of agent and workspace flags. Either
side `enabled=false` kills reflection for that agent.

```json
{
  "enabled": true,
  "interval_iterations": 8,
  "interval_tool_calls": 15,
  "max_prompt_chars": 800,
  "periodic_template": "periodic",
  "tool_call_template": "tool_call"
}
```

When `enabled=false`, runner takes a true zero-IO path: no template
load, no audit, no in-memory bookkeeping.

### Templates

Live under `backend/app/agents/templates/reflection/<name>.md`. Loader
whitelists `periodic` and `tool_call`; rejects path traversal. Placeholders:

| Placeholder              | Meaning                                          |
|--------------------------|--------------------------------------------------|
| `{iteration_count}`      | Current `ReliabilityState.iteration_count`       |
| `{tool_call_count}`      | Cumulative tool calls observed this run          |
| `{recent_tools_summary}` | Most recent 5 tool calls as markdown list        |

After rendering, hard-capped at `max_prompt_chars` (default 800).
Truncation does not raise — just sets `truncated=True` on audit row.

Audit `reflection.injected`: `{kind, run_id, session_id, iteration,
tool_call_count, prompt_chars, truncated}`. Rendered prompt is **not**
stored — auditors can recompute from template + counts. Avoids fanning
out a model-facing instruction string to downstream pipelines.

Adding a custom template requires a code change (whitelist enforced)
— add `.md` file + append slug to `REFLECTION_TEMPLATE_NAMES`.
Whitelisting (not free-form paths) is intentional: reflection templates
run inside the agent loop, attacker-controlled prompt would have system-
prompt blast radius.

---

## Lineage replay (M4.3)

When a compressed summary message folds older turns, lineage metadata
keeps both visible. The runtime reads only the summary (cache-safe);
the trace UI can expand back to originals.

Schema (migration `0060_messages_lineage`, added to `messages`):

| Column                          | Type                                  | Notes                                                                                            |
|---------------------------------|---------------------------------------|--------------------------------------------------------------------------------------------------|
| `compressed_into_summary_id`    | self-FK `messages.id` (`SET NULL`)    | Set on each *original* turn once compaction absorbs it. `SET NULL` (not `CASCADE`) so the original survives if summary is purged. |
| `original_turns_ref`            | JSONB nullable                        | Set on the *summary* message. `{turn_message_ids, turn_count, compressed_at, compaction_strategy}`. |

`COMPACTION_STRATEGIES` in
[`backend/app/db/models/message.py`](../backend/app/db/models/message.py):
`sliding_window` / `manual` / `evolver`. Replay service reads tolerantly
(unknown → `"unknown"`).

**Cache prefix invariant.** Runtime input pipeline reads `summary body
+ tail` unchanged. `original_turns_ref` is **never** appended to
`message_history`. Drawer fetches lazily via separate REST surface.
Neither column is selected when building the prompt.

Routes:

| Method | Path                                                                   | Rate            | Notes                                       |
|--------|------------------------------------------------------------------------|-----------------|---------------------------------------------|
| GET    | `/sessions/{session_id}/messages/{message_id}/lineage`                 | 60/60s          | Resolves summary back to originals. 404 `lineage.not_compressed` when never marked. Audited. |
| GET    | `/sessions/{session_id}/lineage-summaries`                             | 30/60s          | Lists every summary in a session. Not audited (hot path). |

Workspace-member only. Cross-workspace probes → 404 `session.not_found`.

Audit: `lineage.replay_queried` (`summary_message_id`,
`original_turn_count`, `compaction_strategy`),
`lineage.compaction_marked` (reserved for future compaction PR).

Platform settings section `compaction`
([`backend/app/schemas/platform_settings/compaction.py`](../backend/app/schemas/platform_settings/compaction.py)):

| Field                       | Default | Range  | Purpose                                                                       |
|-----------------------------|---------|--------|-------------------------------------------------------------------------------|
| `preserve_lineage`          | `True`  | bool   | Master switch. `False` = compaction falls back to legacy delete.              |
| `max_keep_turns`            | `50`    | 10-500 | Soft target for sliding-window head.                                          |
| `aux_summarize_max_tokens`  | `500`   | 200-2000 | Output budget for aux summarisation call.                                   |

Pure helper `mark_message_as_compressed(summary, originals, *,
strategy=…)` validates strategy slug and returns the dict the caller
assigns to `summary_message.original_turns_ref`. Caller is responsible
for setting each original's `compressed_into_summary_id` and flushing
the session.

---

## Lightweight (no-agent) Flow modes (M0.6)

Default `Flow.execution_mode="agent"` drives the bound agent on every
fire. M0.6 added two `no_agent_*` modes that skip the agent loop
entirely. They still write a `FlowRun` row + audit event so the operator
surface keeps a trail; "silent" only means no notification, channel
fanout, or agent run.

| Mode               | Behaviour                                                                                            |
|--------------------|------------------------------------------------------------------------------------------------------|
| `agent`            | Default. Bound agent runs every fire. `prompt_template` required.                                    |
| `no_agent_script`  | Single shell command in the workspace's Docker sandbox. Empty stdout = silent.                       |
| `no_agent_http`    | One HTTP request; configurable expected statuses. 2xx (or operator list) = silent.                   |

`trigger_config` JSONB shapes:

```jsonc
// no_agent_script
{
  "script_command": "ls /var/log | wc -l",
  "script_cwd": "/workspace",
  "script_timeout_s": 60,             // default 60, max 600
  "script_env": {"FOO": "bar"},
  "escalate_on_nonempty_output": true
}

// no_agent_http
{
  "http_url": "https://example.com/health",   // must pass assert_safe_url
  "http_method": "GET",                       // GET / HEAD / POST
  "http_headers": {"Authorization": "Bearer ${vault://workspace/api_key}"},
  "http_body": null,                          // POST only, max 64 KiB
  "http_timeout_s": 30,
  "http_expected_status": [200, 204],
  "escalate_on_http_failure": true
}
```

Outcomes (`FlowRun.outcome`):

| Outcome             | When                                                              |
|---------------------|-------------------------------------------------------------------|
| `pending`           | Before execution begins.                                          |
| `success`           | Agent mode normal end, or script with empty stdout.               |
| `silent_2xx`        | HTTP probe matched expected-status set.                           |
| `nonempty_output`   | Script returned stdout, `escalate_on_nonempty_output=false`.      |
| `escalated_to_agent`| Probe / script tripped escalate flag — bridged into agent loop.   |
| `http_error`        | HTTP status outside expected set, no escalation.                  |
| `script_error`      | Script exited non-zero.                                           |
| `timeout`           | Script or HTTP probe exceeded timeout.                            |
| `ssrf_blocked`      | URL rejected by SSRF guard.                                       |
| `validation_failed` | `trigger_config` schema failed, or sandbox kind blocked.          |
| `cancelled`         | Operator-cancelled (reserved).                                    |
| `failed`            | Agent run errored out.                                            |

**Sandbox guard.** Script mode delegates to
[`backend/app/agents/harness/sandbox.py`](../backend/app/agents/harness/sandbox.py)
`build_sandbox(...)`. Flow service only accepts kinds `{docker, local}`;
in `APP_ENV=production`, `local` is rejected (`flow.script_local_blocked`)
because it would run arbitrary shell on the SenHarness host. Same rule
as the `shell` tool.

**SSRF guard** (`app.core.url_safety.resolve_safe_url`):

1. Reject scheme outside `{http, https}`.
2. Reject loopback / metadata aliases (`localhost`,
   `metadata.google.internal`, …).
3. Resolve hostname via DNS; reject if any returned address is in a
   private range (`10/8`, `172.16/12`, `192.168/16`, `169.254/16`, IPv6
   ULA/link-local).
4. Pin request to first resolved IP; reuse for the actual fetch —
   defeats DNS rebinding.

Rejection codes: `ssrf.scheme_blocked`, `ssrf.blocked_hostname`,
`ssrf.metadata_endpoint`, `ssrf.private_address`. Audit
`flow.http_ssrf_blocked`.

**Vault templating.** HTTP `http_headers` values and `http_body` may
use `${vault://workspace/<key-name>}`. Resolved at fire time from
`VaultItem.name=<key-name>` filtered by `workspace_id`. Other scopes
(`${vault://platform/...}`) raise `ValueError` — leaked template can
never escape the calling workspace.

**Escalation contract.** When probe / script trips escalate flag:

* New `Session` opened (same as legacy agent path).
* `trigger_payload` enriched with `escalation_context`:
  * Script: `{source: "script", exit_code, stdout_excerpt, duration_ms}`.
  * HTTP: `{source: "http", status, duration_ms}`.
* `prompt_template` can read it as `{{escalation_context.status}}` etc.
* `FlowRun.outcome = escalated_to_agent`.

If flow has no agent / squad / graph target → `validation_failed`.

Audit: `flow.script_executed`, `flow.script_local_blocked`,
`flow.http_executed`, `flow.http_ssrf_blocked`,
`flow.escalated_to_agent`, `flow.validation_failed`,
`flow.test_script` / `flow.test_http` (dry-run).

Stdout / response bodies are **never** persisted in audit metadata —
only lengths, exit codes, status codes, durations.

Routes:

| Method | Route                              | Notes                                              |
|--------|------------------------------------|----------------------------------------------------|
| POST   | `/api/v1/flows`                    | Accepts `execution_mode` + extended `trigger_config`. |
| PATCH  | `/api/v1/flows/{id}`               | Same.                                              |
| POST   | `/api/v1/flows/{id}/test-script`   | Workspace-admin dry-run, 5/min, optional `override` body. |
| POST   | `/api/v1/flows/{id}/test-http`     | Same, 10/min.                                      |
| GET    | `/api/v1/flows/{id}/runs`          | Returns `outcome`, `probe_response_status`, `probe_duration_ms`, `probe_output_excerpt`. |

---

## Job observability (M4.6)

Persistent ARQ task lifecycle log + admin dashboard at
`/settings/system/jobs`. Closes the gap between ARQ's short Redis TTL
on job state and an operator's need to answer "did the overnight
curator finish?".

Schema:

```text
job_runs
├─ job_id             VARCHAR(80)  NOT NULL  -- ARQ job_id
├─ function_name      VARCHAR(100) NOT NULL
├─ workspace_id       UUID NULL
├─ identity_id        UUID NULL
├─ status             VARCHAR(32) NOT NULL DEFAULT 'queued'
├─ enqueued_at / started_at / finished_at  TIMESTAMP
├─ duration_ms        INTEGER NULL
├─ retry_count        INTEGER NOT NULL DEFAULT 0
├─ args_json          JSONB NOT NULL DEFAULT '{}'
└─ error_class / error_message
```

Composite indices: `(function_name, status, finished_at)` (function
detail), `(workspace_id, finished_at)` (workspace-admin scope).

Lifecycle wiring:

```
FastAPI enqueue() → record_job_enqueued (status=QUEUED, best-effort)
                                      ↓
            ARQ on_job_start → job_run_middleware_start → RUNNING + ctx["start_ms"]
                                      ↓
            ARQ on_job_end → legacy on_*_failed_permanent → job_run_middleware_end
                                      ↓
                        SUCCESS / FAILED / FAILED_PERMANENT
```

The legacy failure dispatcher runs **first** so per-task hooks land
exactly as before; the middleware end hook is **chained** to apply the
terminal status.

### Args redaction + truncation

Before persisting `args_json`:

1. Recursively walk `(args, kwargs)`.
2. Substring-match each key against
   `SENSITIVE_KEY_FRAGMENTS` (`password` / `token` / `secret` /
   `api_key` / `client_secret` / `access_key` / `private_key` / `auth`
   / `credential` / `bearer` / `cookie` / `session_id` / `x-api-key` and
   variations).
3. Replace value under matching key with `"***"`.
4. Cap recursion at 6 levels.
5. Serialise; if > 4 KB → replace payload with `{"_truncated": true,
   "_size_bytes": N}`. Retry endpoint refuses with
   `409 args_truncated_cannot_replay`.

`error_message` capped at 4 K chars; oversize → trailing 16 chars
replaced with `…[truncated]`.

### Retention policy

Per-row, asymmetric:

| Status              | Retention                                         |
|---------------------|---------------------------------------------------|
| `success`           | 60 days after `finished_at`                       |
| `failed`            | indefinite (forensics)                            |
| `failed_permanent`  | indefinite (forensics)                            |
| `queued` / `running`| indefinite while non-terminal                     |

Daily M0.11 `retention_physical_purge` runs `_purge_job_runs`. Honours
`system_settings.retention.physical_purge_enabled` — `False` keeps the
pass in dry-run with "would purge N rows" audit.

Rationale: failure rows are root-cause artefacts; pruning them on the
same TTL as success rows would silently delete forensics. 100 k failure
rows at ~1 KB ≈ 100 MB lifetime — well below noise.

### Scope + endpoints

```
                ┌───────────────────────────────────────┐
                │  /admin/jobs/queues / recent / health │
                ├──────────────────┬────────────────────┤
                │ platform admin   │ workspace admin    │
                │ (PLATFORM_ADMIN) │ (owner / admin)    │
                ├──────────────────┼────────────────────┤
   workspace_id │ NULL — sees every│ X-Workspace-Id     │
        scope   │ workspace + plat │ — sees that        │
                │ form-wide rows   │ tenant's rows only │
                └──────────────────┴────────────────────┘

POST /admin/jobs/{id}/retry — platform admin only.
```

| Method | Path                          | RBAC                          | Rate                           |
|--------|-------------------------------|-------------------------------|--------------------------------|
| GET    | `/admin/jobs/queues`          | platform / workspace admin    | `admin_jobs_queues 30/60s`     |
| GET    | `/admin/jobs/recent`          | platform / workspace admin    | `admin_jobs_recent 60/60s`     |
| GET    | `/admin/jobs/health`          | platform / workspace admin    | `admin_jobs_health 30/60s`     |
| POST   | `/admin/jobs/{job_id}/retry`  | platform admin only           | `admin_jobs_retry 5/60s`       |

`/recent` filters: `?status=...`, `?function_name=...`, `?limit=1..500`.
`/queues` + `/health` accept `?window_seconds=60..86400` (default 3600).

### Manual retry

1. Admin clicks "Retry" on `failed_permanent` row.
2. POST `/admin/jobs/{id}/retry`.
3. Server reads persisted JobRun; status must be FAILED /
   FAILED_PERMANENT (else 409); `args_json` must not be truncation
   sentinel (else 409).
4. Server reconstructs `(args, kwargs)` and calls
   `app.worker.queue.enqueue` with original function name + workspace /
   identity from original row.
5. Audit `job.retry_triggered_by_admin` with `{original_job_id,
   new_job_id, function_name, original_status, original_retry_count}`.
6. New job lands with fresh `job_id`, full middleware chain — fresh
   `job_runs` row under new id; original stays untouched as forensic
   evidence.

---

## Cron slot map

Every recurring job in one place so a new sweep doesn't clash:

| Slot                       | Job                                        | Doc reference                                 |
|----------------------------|--------------------------------------------|-----------------------------------------------|
| `minute={0,5,…,55}`        | Retention cascade (M0.11)                  | extensions-and-governance.md#retention        |
| `minute={2,32}`            | Pending memory sweep (M0.7)                | this doc — Memory                             |
| `minute={4,14,24,34,44,54}`| Inflight runs reaper (M2.5.2)              | this doc — Inflight runs                      |
| `minute={6,36}`            | Hub auto-pull sweep (M3.3)                 | skills.md#hub-catalog                         |
| `minute={7,37}`            | Verifier (M2.4)                            | skills.md#auto-verifier                       |
| `minute={15}`              | Judge periodic sweep (M0.3)                | this doc — Run quality judge                  |
| `second={0}` (every minute)| Subagent zombie reaper (M2.5.1)            | this doc — Sub-agent reliability              |
| `02:30 daily`              | Skill usage rollup (M1.3)                  | skills.md#usage-telemetry                     |
| `03:15 daily`              | Skill curator (M1.4)                       | skills.md#curator                             |
| `03:30 daily`              | Cleanup window (M0.10)                     | extensions-and-governance.md                  |
| `04:00 daily`              | Retention physical purge (M0.11)           | extensions-and-governance.md#retention        |
| `04:30 daily`              | Evolver workflow sweep (M2.3)              | this doc — Evolver workflow                   |
| `04:45 daily`              | Checkpoint GC (M2.5.2)                     | this doc — Inflight runs                      |
