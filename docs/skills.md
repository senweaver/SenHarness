# Skills

SkillPack is the workspace's authored unit of agent capability. This page
covers the full skill subsystem: lifecycle, version snapshots, runtime
discovery + cap, telemetry, the curator/verifier nightly sweeps, the hub
federation layer, the sanitizer, the lineage graph, and the diff renderer.

```
authoring         lifecycle           runtime               sweeps              federation
─────────         ─────────           ───────               ──────              ──────────
PATCH/version  →  9-state pack    →   discovery + cap   →   curator (nightly)   hub catalog
                  5-state version     usage telemetry       verifier (30 min)   sanitizer
                                                                                  promote / pull
                                          ↓
                                   lineage edges (graph view)
```

Reference modules: [`backend/app/db/models/skills.py`](../backend/app/db/models/skills.py),
[`backend/app/services/skill_*.py`](../backend/app/services),
[`backend/app/agents/harness/skills.py`](../backend/app/agents/harness/skills.py).

---

## Concept lifecycle (`SkillPack.state`)

A pack is a *concept* with a 9-state machine plus an orthogonal `pinned`
boolean. Edges are whitelisted in
[`skill_lifecycle.ALLOWED_TRANSITIONS`](../backend/app/services/skill_lifecycle.py);
`TOMBSTONE` is terminal.

| State        | Meaning                                                   |
|--------------|-----------------------------------------------------------|
| `draft`      | Author WIP. Not visible to agents.                        |
| `candidate`  | Submitted; awaiting evaluation.                           |
| `active`     | In use by agents. Default state for fresh packs.          |
| `stale`      | Idle long enough that the curator may propose archiving.  |
| `pinned`     | Legacy state; current pattern is `active + pinned=True`.  |
| `archived`   | Hidden from agents. Restorable.                           |
| `superseded` | Replaced by a newer pack (evolver path).                  |
| `deprecated` | Marked for removal but still accessible.                  |
| `rejected`   | Candidate review denied; will eventually tombstone.       |
| `tombstone`  | Permanently retired. Slug locked in `tombstone_slugs`.    |

Allowed edges (summary):

```
DRAFT       -> CANDIDATE | ARCHIVED
CANDIDATE   -> ACTIVE | REJECTED
ACTIVE      -> STALE | PINNED | SUPERSEDED | DEPRECATED | ARCHIVED
STALE       -> ACTIVE | ARCHIVED | PINNED
DEPRECATED  -> ARCHIVED
SUPERSEDED  -> ARCHIVED
ARCHIVED    -> ACTIVE | TOMBSTONE
REJECTED    -> TOMBSTONE
TOMBSTONE   -> {}
```

Forbidden edges raise `skill.invalid_transition` (HTTP 409); a transition
out of `TOMBSTONE` raises `skill.terminal_state`.

**Pinned exemption.** `pinned=True` packs are exempt from automatic flows
(curator, evolver). The lifecycle service raises
`PackPinnedAutoSkipped` when an auto-flow calls
`transition(..., bypass_pinned=False)`. User/admin API verbs always pass
`bypass_pinned=True`.

**Tombstone slug lock.** On `TOMBSTONE` a row lands in `tombstone_slugs`;
`POST /skills/packs` rejects with `skill.slug_tombstoned` on slug reuse
inside the same workspace. `tombstone_slugs` is excluded from retention
cascade.

### Verb API (`/api/v1/skills/packs`)

| Method | Path                              | RBAC   | Rate bucket                               |
|--------|-----------------------------------|--------|-------------------------------------------|
| POST   | `/{pack_id}/pin`                  | member | `skill_lifecycle_action` 30/60s           |
| POST   | `/{pack_id}/unpin`                | member | `skill_lifecycle_action` 30/60s           |
| POST   | `/{pack_id}/archive`              | member | `skill_lifecycle_action` 30/60s           |
| POST   | `/{pack_id}/restore`              | member | `skill_lifecycle_action` 30/60s           |
| POST   | `/{pack_id}/deprecate`            | member | `skill_lifecycle_action` 30/60s           |
| POST   | `/{pack_id}/transitions`          | admin  | `skill_lifecycle_admin_action` 20/60s     |
| GET    | `/{pack_id}/state`                | member | `skill_lifecycle_read` 60/60s             |
| GET    | `/{pack_id}/transitions`          | member | `skill_lifecycle_read` 60/60s             |

Verb body is optional `{"reason": "<text>"}`. The transitions route also
requires `target_state`. History reads straight from `audit_events`
filtered by `action='skill.transitioned'` + `resource_id`.

---

## Version snapshots (`skill_pack_versions`)

Every persistent body change lands as an immutable row. `SkillPack` is a
*cache* mirroring whichever version currently holds `state=ACTIVE`. The
version table has its own 5-state machine independent of the concept
lifecycle:

```
PROPOSED -> VALIDATING -> ACCEPTED -> ACTIVE -> RETIRED
    │           │
    └→ REJECTED ┘   (terminal)
```

`ACTIVE` is unique per `pack_id`; activating retires the previous active
row in the same transaction. `REJECTED` and `RETIRED` are terminal.

Key columns (`workspace_id` always present; FK `pack_id` ON DELETE CASCADE):

| Column                       | Notes                                                     |
|------------------------------|-----------------------------------------------------------|
| `version_no`                 | Unique per pack (`uq_skill_pack_versions_pack_no`).       |
| `content_hash`               | sha256 over body + sorted file map; unique per pack.      |
| `content_md`                 | Full SKILL.md body snapshot.                              |
| `files_json`                 | `{path: sha256}` for ancillary files.                     |
| `state`                      | One of the 5 states above.                                |
| `created_by`                 | `user` / `evolver` / `hub_pull` / `migration`.            |
| `creator_identity_id`        | Nullable for non-human authors.                           |
| `source_run_ids`             | Run IDs that motivated the proposal.                      |
| `judge_score`                | Aux judge grade.                                          |
| `validation_results`         | Verifier output (lint / replay deltas).                   |
| `superseded_by_version_id`   | Pointer to the version that retired this one.             |
| `activated_at` / `retired_at`| Stamped atomically when activate retires the prior row.   |

The `(pack_id, content_hash)` constraint is bytewise dedup — identical
proposals raise `409 skill_version.duplicate_content_hash`.

### Service surface ([`backend/app/services/skill_version.py`](../backend/app/services/skill_version.py))

* `compute_content_hash(content_md, files)` — pure helper matching the
  dedup constraint.
* `create_version(...)` — insert PROPOSED, audit `skill_version.created`.
* `activate_version(...)` — retire current ACTIVE, mark target ACTIVE,
  mirror `content_md` / `content_hash` back onto `SkillPack` so the
  legacy read path (`GET /skills/packs/{id}/content`) works unchanged.
* `transition_version(...)` — drive the state machine.
* `rollback_to_version(...)` — re-promote a historical version with an
  extra `skill_version.rollback` audit row (M1.6).

Caller commits. `PATCH /skills/packs/{id}` snapshots whenever
`content_md` / `files_json` is supplied; identical content silently
dedup's.

### Routes

| Method | Route                                                          | RBAC   | Rate bucket                  |
|--------|----------------------------------------------------------------|--------|------------------------------|
| GET    | `/skills/packs/{pack_id}/versions`                             | member | `skill_version_read 60/60s`  |
| GET    | `/skills/packs/{pack_id}/versions/active`                      | member | same                         |
| GET    | `/skills/packs/{pack_id}/versions/{version_no}`                | member | same                         |
| POST   | `/skills/packs/{pack_id}/versions/{version_id}/activate`       | admin  | `skill_version_write 20/60s` |
| POST   | `/skills/packs/{pack_id}/versions/{version_id}/transition`     | admin  | same                         |
| POST   | `/skills/packs/{pack_id}/versions/{version_id}/rollback`       | admin  | `skill_version_rollback 10/60s` |
| GET    | `/skills/packs/{pack_id}/versions/{a}/diff/{b}`                | member | `skill_diff_compute 30/60s`  |

The diff endpoint accepts `"active"`, `"latest"`, a numeric
`version_no`, or a UUID for either label.

### Audit actions

| Action                       | Metadata                                                                         |
|------------------------------|----------------------------------------------------------------------------------|
| `skill_version.created`      | `pack_id`, `slug`, `version_no`, `content_hash`, `created_by`, `source_run_ids`  |
| `skill_version.activated`    | `pack_id`, `version_no`, `previous_active_version_*`, `reason`                   |
| `skill_version.retired`      | `pack_id`, `version_no`, `superseded_by_version_*`                               |
| `skill_version.transitioned` | `pack_id`, `version_no`, `from`, `to`, `reason`                                  |
| `skill_version.rollback`     | `pack_id`, `target_version_id`, `target_version_no`, `reason`                    |

Rollback fires `activated` + `retired` + `rollback` in the same
transaction. Idempotent when target is already ACTIVE.

---

## Runtime discovery

At the start of every run,
[`build_skills_capability`](../backend/app/agents/harness/skills.py)
materialises the bound packs into a `SkillsCapability` for
`pydantic_ai_skills`:

1. Read `policy["skills"]` (forwarded from `agent.metadata_json["skills"]`
   as a list of pack-id strings).
2. Validate UUIDs; malformed entries → `skills.malformed_pack_id` warn.
3. Call
   [`SkillPackRepository.list_active`](../backend/app/repositories/skills.py)
   — keeps rows where `state=ACTIVE` **or** `pinned=True`, excludes
   `TOMBSTONE` either way.
4. Load `SKILL.md` body from the per-pack `skill_files` row (falls back
   to `description`).
5. Wrap in `SkillsCapability`, return `(capability, injected_pack_ids)`.

Eligibility matrix:

| State        | `pinned=False` | `pinned=True` |
|--------------|----------------|---------------|
| `DRAFT`      | excluded       | included      |
| `CANDIDATE`  | excluded       | included      |
| `ACTIVE`     | included       | included      |
| `STALE`      | excluded       | included      |
| `ARCHIVED`   | excluded       | included      |
| `SUPERSEDED` | excluded       | included      |
| `DEPRECATED` | excluded       | included      |
| `REJECTED`   | excluded       | included      |
| `TOMBSTONE`  | excluded       | excluded      |

The resolver never raises into the agent loop — every error path returns
`(None, [])` and logs at the appropriate level. The `(None, [])`
contract makes the capture pipeline backend-kind neutral; OpenClaw and
other remote adapters never produce telemetry for this metric.

`list_active` is `ORDER BY updated_at DESC` independent of policy order.
This deterministic order keeps the `SystemPromptPart` prefix bytes
stable across consecutive runs so provider-side prompt cache hits don't
flip. Locked by
[`backend/tests/unit/agents/test_skill_capability_cache_safety.py`](../backend/tests/unit/agents/test_skill_capability_cache_safety.py).

### Hard cap ([`backend/app/agents/harness/skills.py`](../backend/app/agents/harness/skills.py))

Between resolution and materialisation, `select_active_set` enforces a
`(count, chars)` ceiling. Pinned packs bypass the cap; unpinned packs
are reordered by strategy and greedily packed until either ceiling is
hit. Dropped packs get one `skill_usage` row with
`event_kind=DROPPED_AT_CAP`, plus a `skill.cap_applied` audit row.

Config resolution: workspace override → platform default → hard-coded:

| Key                          | Workspace path                                              | Platform path                                                          | Hard default                  |
|------------------------------|-------------------------------------------------------------|------------------------------------------------------------------------|-------------------------------|
| `max_active_injected`        | `workspaces.home_config_json.skills.max_active_injected`    | `system_settings.skill_injection_defaults.max_active_injected`         | `30`                          |
| `max_injected_chars_total`   | `…skills.max_injected_chars_total`                          | `…skill_injection_defaults.max_injected_chars_total`                   | `12000`                       |
| `selection_strategy`         | `…skills.selection_strategy`                                | `…skill_injection_defaults.selection_strategy`                         | `effectiveness_then_recency`  |

Strategies:

* **`effectiveness_then_recency`** (default): sort by
  `(effectiveness_avg DESC NULLS LAST, last_used_at DESC NULLS LAST,
  created_at ASC, slug ASC)`.
* **`manual_only`**: preserve the caller's input order verbatim.

Char counting is `len(SKILL.md body)` (fallback to `description`).
`files_json` attachments do not count; they're addressable via runtime
filesystem tools, not the always-on prompt. The cap is static — no
dynamic adjustment by observed token pressure.

When the pinned set alone exceeds the count cap, a warn-level
`skill.cap_pinned_above_count_cap` log fires but no pinned pack is
dropped. The cap is never the reason an agent run fails: every audit /
telemetry write is fail-open.

---

## Usage telemetry (`skill_usage`)

One row per observable event, aggregated into the `SkillPack.last_used_at`
and `effectiveness_avg` columns and rendered in the admin "Usage" tab.

| Event kind        | When                                                                       |
|-------------------|----------------------------------------------------------------------------|
| `injected`        | Pack put into the run prompt.                                              |
| `read_full`       | Agent issued a full-body read (e.g. `read_skill(slug=..)`).                |
| `used_in_tool`    | Tool call inside the run cited the pack (judge-detected).                  |
| `patched`         | Runtime modified the pack body in-flight (curator PATCH-on-error).         |
| `dropped_at_cap`  | Context-window cap excluded the pack from the prompt this iteration.       |

`contribution_score` is set asynchronously by the M0.3 judge / curator
and stays NULL for unscored rows. The aggregator skips NULL rows.

Pipeline (capture site → telemetry):

```
build_skills_capability ──→ NativeBackend._injected_skill_ids[run_id]
                                              │
                                  (run loop emits frames)
                                              ▼
session_artifacts.injected_skill_pack_ids  +  skill_usage(event=INJECTED)
                          ↓                                  ↓
                  audit: artifact.captured       audit: skill.usage_batch_recorded
```

`NativeBackend.get_injected_skill_ids(run_id)` is the read hook;
capture sites use private `_read_injected_skill_ids` helpers
(`app/api/v1/sessions.py` for WS, `app/services/agent_runner.py` for
channel/flow). The helpers tolerate missing hook attrs / raises /
`backend=None` → `[]`. Cleanup happens in `NativeBackend.run`'s
`finally` block so a long-running process never leaks entries.

Routes (all under `/api/v1/skills/packs/{pack_id}`):

| Method | Route             | RBAC   | Rate bucket               |
|--------|-------------------|--------|---------------------------|
| GET    | `/usage`          | member | `skill_usage_read 60/60s` |
| GET    | `/usage/stats`    | member | same                      |
| POST   | `/usage/rollup`   | admin  | `skill_usage_admin 5/300s`|

The ARQ rollup `skill_telemetry.rollup_skill_usage` runs **02:30 UTC
daily** and only touches packs whose `last_used_at` is older than 24 h
(or NULL). Live `record_usage_batch` calls keep hot packs current.

Audit actions: `skill.usage_recorded` (admin/test single insert),
`skill.usage_batch_recorded` (runtime capture, 1 per run),
`skill.stats_rolled_up` (per-pack aggregate), `skill.usage_recording_failed`
(telemetry breakage breadcrumb — capture/judge/promote still work).

`record_usage_batch` does **not** dedup on `(workspace_id, run_id,
pack_id, event_kind)` — a WS reconnect that double-captures will create
duplicate INJECTED rows. The rollup deduplicates by `MAX(created_at)` so
`last_used_at` and `effectiveness_avg` stay correct.

`skill_usage` is in `CASCADE_TARGETS` with `workspace_scoped=True,
identity_scoped=True`. Cascade soft-delete physically removes rows; the
daily physical purge skips this table (no `deleted_at`).

---

## Curator (nightly, 03:15 UTC)

[`backend/app/jobs/curator.py`](../backend/app/jobs/curator.py) +
[`backend/app/services/skill_curator.py`](../backend/app/services/skill_curator.py).
The Curator never deletes anything directly and always honours pin.

Per tick, per non-deleted workspace:

1. **Stale sweep.** `ACTIVE` packs whose `last_used_at` is older than
   `stale_after_days` (default 30) AND `min_idle_hours` (default 24) →
   transition to `STALE`. Pinned packs raise `PackPinnedAutoSkipped`;
   the counter ticks and the sweep continues.
2. **Archive proposal.** `STALE` packs whose `state_changed_at` is older
   than `archive_after_days` (default 90) → file an `Approval` row
   (`resource_type='skill_pack_archive'`, `expires_at=now+7d`). **Not
   archived yet** — `curator_apply_approved` flips state only after an
   admin approves.
3. **Audit.** One `curator.swept` row per workspace summarises counts.

```
ACTIVE -- stale_after_days idle  --> STALE
STALE  -- archive_after_days     --> Approval(skill_pack_archive)
                                          │
                                          ├ approved → ARCHIVED
                                          ├ denied   → STALE (no change)
                                          └ expired  → auto-archive (TTL processor)
```

Config (workspace override → platform default → hard default):

| Key                       | Default | Range   | Meaning                                                          |
|---------------------------|---------|---------|------------------------------------------------------------------|
| `enabled`                 | `true`  | bool    | Master switch.                                                   |
| `stale_after_days`        | `30`    | 1–3650  | Idle days before ACTIVE → STALE.                                 |
| `archive_after_days`      | `90`    | 1–3650  | Days in STALE before archive proposal.                           |
| `min_idle_hours`          | `24`    | 0–720   | Race guard against fresh use events the rollup hasn't materialised. |
| `active_skills_soft_cap`  | `50`    | 1–10000 | Reserved; M1.8 hard cap is the actual enforcement.               |

Cross-field invariant: `stale_after_days <= archive_after_days`.
Reversing would file archive proposals on packs that just transitioned
to STALE.

Cron slot 03:15 sits between the 02:30 usage rollup (fresh
`last_used_at`) and 03:30 cleanup.

**Workspace config UI** lives at `/settings/workspace/skills` ("Skills
(Curator)"). `GET /workspaces/{id}/settings/curator` returns a
`source` map per knob (`"workspace"` / `"platform_default"`) so the UI
badges customised knobs. `POST /settings/curator/run-now` lets admins
trigger a synchronous tick (2/300s rate bucket, audits
`curator.run_now_triggered`).

Failure scope: per-pack errors caught + logged + sweep continues;
workspace errors counted + sweep continues to next workspace; ARQ
exhaustion writes `job.failed_permanent` after 3 strikes.

Approval body shape:

```json
{
  "kind": "skill_pack_archive",
  "pack_id": "<uuid>",
  "slug": "<slug>",
  "reason": "curator: stale for >= 90 days",
  "stale_since": "<ISO8601>",
  "last_used_at": "<ISO8601 or null>",
  "use_count_30d": <int>
}
```

`tool_name` is the sentinel `"_skill_pack_archive"`; non-tool approvals
reuse the table via `resource_type`.

Audit: `curator.swept`, `curator.archive_proposed`, `curator.archived`,
`skill.transitioned`, `skill.transition_skipped_pinned`.

---

## Auto-verifier (30 min, minute 7/37)

[`backend/app/services/skill_verifier.py`](../backend/app/services/skill_verifier.py).
Gate that turns a `PROPOSED` `SkillPackVersion` into `ACCEPTED` (ready
for approval) or `REJECTED`. **Never publishes** — `ACCEPTED → ACTIVE`
is owned by the approval pipeline.

Approach: judge-replay rather than full agent re-run (10× cheaper aux
tokens). For each candidate version:

1. Pull recent `SessionArtifact` rows likely to be affected.
2. Re-judge each trace twice with the aux LLM — once with the **old**
   SKILL body, once with the **new** body — and diff score means.
3. `mean(new) - mean(old) >= min_score_delta` → ACCEPTED; else REJECTED.

State flow:

```
PROPOSED → VALIDATING → ACCEPTED   (delta ≥ threshold OR skipped_insufficient)
                  │
                  └→ REJECTED       (delta < threshold OR errored)
```

Config (`EvolverSettings.auto_verifier` — platform default → workspace):

| Field                  | Default | Description                                                     |
|------------------------|---------|-----------------------------------------------------------------|
| `enabled`              | `True`  | Master switch.                                                  |
| `min_score_delta`      | `0.05`  | `mean(new) - mean(old)` floor for ACCEPTED. Range `[0.0, 1.0]`. |
| `min_replay_artifacts` | `3`     | Below this, ACCEPTED with `validation_skipped=True`.            |

When the artifact pool is too small, the verifier accepts with the
skipped flag (banner in the M2.5 review card). Rejecting unverifiable
candidates would silently kill every brand-new skill.

**Relevant-artifact heuristics** (any match wins):

1. `injected_skill_pack_ids` JSONB array contains the pack id (strongest
   signal — runtime loaded it).
2. `invoked_tools` array contains the slug.
3. `turns_json::text ILIKE '%<slug>%'` (noisy fallback for first-version
   proposals).

Matching runs in Python over a recent-history window so the verifier
doesn't depend on a JSONB-search index path that varies by Postgres
minor. Trace is JSON-dumped and trimmed to 8 KB (head); the tail of a
long failure loop is mostly retries.

**Breaker** (key `verifier:fail:<workspace_id>`): 3 consecutive errors
trip, 1800 s recovery TTL, 3 successes reset. Independent of judge and
evolver breakers.

Routes:

| Method | Path                                                                    | RBAC   | Rate     |
|--------|-------------------------------------------------------------------------|--------|----------|
| POST   | `/api/v1/skills/packs/{pack_id}/versions/{version_id}/verify-now`       | admin  | `5/300s` |
| GET    | `/api/v1/skills/packs/{pack_id}/versions/{version_id}/validation`       | member | `60/60s` |

`verify-now` runs synchronously; already-VALIDATING/ACCEPTED/REJECTED →
`409 skill_verifier.invalid_state`. `validation` returns `{}` for
never-verified versions (UI renders a placeholder).

Audit: `verifier.started`, `verifier.completed`, `verifier.skipped_insufficient`,
`verifier.errored`, `verifier.breaker_tripped`, `verifier.failed_permanent`.

---

## Diff renderer

[`backend/app/services/skill_diff.py`](../backend/app/services/skill_diff.py)
— pure-function unified-diff helpers, side-effect free, no DB. Used by
`POST /skills/diff`, the versioned `/versions/{a}/diff/{b}` route, and
the evolver approval UI.

```python
result = render_unified_diff(
    old_content, new_content,
    context_lines=3,
    file_label="SKILL.md",
    from_label="old", to_label="new",
)
result.diff           # str — full unified diff
result.stats          # DiffStats(added_lines, removed_lines, hunks)
result.files_changed  # ["SKILL.md"]
```

`render_multi_file_diff(old_map, new_map)` for files-keyed inputs.
Identical files are skipped; output is per-file blocks separated by a
blank line. Adds/removes encoded against `/dev/null` per git convention.

`truncate_diff_for_display(diff)` returns `(text, was_truncated)`,
defaults **2000 lines / 80 KB**. A typical SKILL.md body (<200 lines)
never trips truncation; a wholesale rewrite (200 K chars × 2) is
bounded at ~80 KB out.

`POST /skills/diff`: per-side cap 200 KB (422 above); rate bucket
`skill_diff_compute 30/60s`. `skill.diff_truncated` audit fires only
when display truncation hit. Workspace-member only.

Frontend: `SkillDiff` (low-level) wraps `react-diff-viewer-continued`
via `next/dynamic({ ssr: false })`. `SkillDiffPanel` is the API-aware
wrapper — picks the inline path (raw content) or versioned path
(`packId` + `versionA/B`).

---

## Privacy sanitizer

[`backend/app/services/skill_sanitize.py`](../backend/app/services/skill_sanitize.py)
sits between a workspace pack body and `HubSkillPackVersion`. Single
choke point that strips workspace-identifying tokens before any
cross-workspace upload.

Redaction order (deterministic):

1. **Email addresses** (`[\w.+-]+@[\w-]+\.[\w.-]+`) → `[email-redacted]`.
2. **URLs containing the workspace slug** → `https://<host>/[ws-slug-redacted]`.
   URLs that don't mention the slug pass through.
3. **Bare workspace-slug occurrences** anywhere in the body →
   `[ws-slug-redacted]`. Case-insensitive.
4. **Person names** when a PII detector is wired (see below) →
   `[name-redacted]`.
5. **Workspace-defined `extra_redaction_patterns`** — each regex applied
   as one extra layer; invalid patterns dropped silently.

After the body pass, every `source_run_id` is collapsed to the first 16
hex of `SHA-256(workspace_id + run_id)`. The workspace-id salt prevents
hub auditors from correlating sessions across tenants.

PII detector resolution order:

1. `pydantic_ai_shields.PiiDetector` (same library that backs the
   runtime shields).
2. `presidio-analyzer.AnalyzerEngine` (fallback).
3. None → name pass silently skipped.

Workspace config (`Workspace.home_config_json["hub_promotion"]`):

```json
{
  "hub_promotion": {
    "extra_redaction_patterns": ["PROJECT-[A-Z]+", "CONFIDENTIAL"],
    "skip_pii_detection": false
  }
}
```

`SanitizationStats` reports `redacted_emails`, `redacted_urls`,
`redacted_paths`, `redacted_pii`, `redacted_extra`,
`run_id_hashed_count`, `failure_reason`.

**Failure-safe contract.** `sanitize_for_hub` never raises. On internal
error it returns the input bytes verbatim with
`stats.failure_reason` set. `HubSettings.sanitizer_required=True`
(default) makes a failure a hard blocker
(`BLOCKER_SANITIZER_REQUIRED_FAILED` in `preview_promotion`).

Audit: `hub.sanitize.previewed`, `hub.sanitize.failed`,
`hub.sanitize.blocked_by_required`.

**Threat model — what it does NOT catch:** binary attachments, indirect
paraphrase ("the head of Engineering at our parent company"), or
run-id-count ordering (intentional — we obscure lookup, not provenance).
Defence-in-depth comes from the evolver review + the promote-time admin
gate.

---

## Hub catalog (federation)

The Hub is the federation layer above per-workspace SkillPacks.
Workspaces stay isolated tenants but can subscribe to and pull packs
from a shared catalog.

Scopes:

* **PLATFORM** (`tenant_id IS NULL`) — visible to every workspace.
  Promotion to PLATFORM requires `platform_role == PLATFORM_ADMIN`.
* **TENANT** (`tenant_id` required) — visible only to workspaces sharing
  the tenant. Default scope on a fresh promotion.

`hub_skill.resolve_caller_tenant` derives tenant from the calling
workspace, falling back to `workspace.id` until a dedicated `tenants`
table lands.

Hub-side lifecycle (narrower than workspace machine):

```
ACTIVE ⇄ DEPRECATED  →  ARCHIVED  →  TOMBSTONE   (terminal)
                            ↑
                         ACTIVE
```

Slug uniqueness: `UNIQUE(scope, tenant_id, slug)` plus a partial unique
index `(slug) WHERE scope='platform' AND tenant_id IS NULL` (Postgres
treats NULL as distinct).

Tables (migration 0053): `hub_skill_packs`, `hub_skill_pack_versions`
(immutable; `is_active` boolean — no validation step on the hub side
because sanitization + admin promotion happen *before* the row lands),
`workspace_hub_subscriptions` (workspace ↔ pack edges with `auto_pull`
flag + `last_pulled_version_no` cursor).

Routes:

| Method | Path                                              | Auth                    | Rate bucket                |
|--------|---------------------------------------------------|-------------------------|----------------------------|
| GET    | `/skills/hub`                                     | workspace member        | `hub_catalog_read 60/60s`  |
| GET    | `/skills/hub/{id}`                                | member + visible        | same                       |
| GET    | `/skills/hub/{id}/versions`                       | member + visible        | same                       |
| GET    | `/skills/hub/{id}/versions/active`                | member + visible        | same                       |
| POST   | `/admin/skills/hub/{id}/transition`               | platform / tenant admin | `hub_admin_transition 10/60s` |

Platform settings (`HubSettings` under `/admin/settings/hub`):

| Field                                | Default | Effect                                                                |
|--------------------------------------|---------|-----------------------------------------------------------------------|
| `enabled`                            | `True`  | When `False`, every hub endpoint returns `hub.disabled` (403).        |
| `default_scope`                      | `tenant`| Scope chosen when promote omits scope.                                |
| `require_admin_for_platform_promote` | `True`  | Reserved (route already enforces).                                    |
| `auto_pull_enabled_default`          | `False` | Default for new `WorkspaceHubSubscription.auto_pull`.                 |
| `sanitizer_required`                 | `True`  | Pull refuses a hub pack until the sanitizer is wired.                 |

### Promote / Subscribe / Pull (M3.3)

The four verbs:

1. **Promote** (`POST /skills/packs/{id}/promote-to-hub`, workspace admin,
   `hub_promote_initiate 5/300s`) — gated by a 30-day approval so a
   workspace can never accidentally publish.
2. **Subscribe** (`POST /skills/hub/{id}/subscribe`, admin, idempotent) /
   **Unsubscribe** (DELETE) — toggles `auto_pull` rather than creating a
   duplicate row.
3. **Pull** (`POST /skills/hub/{id}/pull`, admin, `hub_pull_manual 10/300s`)
   — translates the hub's currently active version into a local
   `SkillPack(state=DRAFT, source=IMPORTED, enabled=False)` plus
   `SkillPackVersion(state=PROPOSED, created_by='hub_pull')`. The
   candidate still flows through the M2.4 verifier — pull never bypasses
   approval.
4. **Auto-pull sweep** — ARQ task ticks at minute `{6, 36}`. Per-subscription
   isolation: 3 attempts per subscription, then
   `hub.auto_pull_failed_permanent` and skip past so head-of-line
   failures don't block the rest of the workspace's sweep.

Promote → approve → apply flow:

```
POST /promote-to-hub
   ↓
initiate_promotion → preview_promotion (sanitize + dedup + scope eligibility)
   ↓                   blockers? → 403 / 409
Approval row (resource_type=hub_promotion, expires_at = now + 30d)
   ↓
admin POST /approvals/{id}/decision (approve)
   ↓
dispatch_approved_approval → _apply_hub_promotion → apply_promotion
   ↓
re-run preview · insert/reuse HubPack · insert/dedup Version
retire previous active · back-subscribe source workspace (auto_pull)
audit: hub.promotion_applied
```

The three-check eligibility gate (route / preview / apply) means a user
demoted between propose and apply cannot push the row through.

**Dedup at apply.** When `preview.will_dedup_against` is set, apply does
not create a new hub version — it activates the existing target version
and back-subscribes the source workspace. Two workspaces that
independently authored byte-identical bodies converge on one row.

Manual pull behaviour: subscription must exist (404 otherwise); hub
pack must be visible to caller tenant (404 even if subscription row
exists). When `last_pulled_version_no` matches active → `status='up_to_date'`
+ `hub.pulled_skipped_up_to_date` audit. Otherwise create/reuse local
draft + PROPOSED version, update cursor, audit `hub.pulled`.

Audit keys: `hub.promotion_proposed`, `hub.promotion_applied`,
`hub.promotion_rejected`, `hub.subscription.created`,
`hub.subscription.deleted`, `hub.pulled`, `hub.pulled_skipped_up_to_date`,
`hub.auto_pull_failed_permanent`, `hub.auto_pull_sweep_failed_permanent`.

`workspace_hub_subscriptions` is in `CASCADE_TARGETS`; workspace
soft-delete hard-deletes the rows. Hub pack + version rows live above
the workspace layer and survive.

---

## Lineage graph

`skill_lineage_edges` records the **true** derivation between packs —
not vector similarity. Lives in a relation table, not in embeddings.

Edge kinds:

| Kind             | Parent              | Child           | Written by                                                |
|------------------|---------------------|-----------------|-----------------------------------------------------------|
| `derived_from`   | parent SkillPack    | child SkillPack | evolver `propose_skill_*` after a candidate version lands |
| `supersedes`     | old SkillPack       | new SkillPack   | lifecycle when `superseded_by_pack_id` is set             |
| `forked_from`    | source SkillPack    | new SkillPack   | reserved for admin fork verb                              |
| `pulled_from_hub`| **NULL** (hub side) | local SkillPack | hub `pull_now` after local materialisation                |

`parent_pack_id` is nullable specifically for `pulled_from_hub` — the
parent lives on the hub catalog. The graph builder synthesises an
external placeholder node from `hub_pack_slug` (no source-workspace
metadata leaks).

Table:

```
skill_lineage_edges
  workspace_id   UUID NOT NULL                       FK workspaces ON DELETE CASCADE
  parent_pack_id UUID                                FK skill_packs ON DELETE CASCADE (NULLable)
  child_pack_id  UUID NOT NULL                       FK skill_packs ON DELETE CASCADE
  edge_kind      VARCHAR(32) NOT NULL
  derived_from_run_ids JSONB NOT NULL DEFAULT '[]'   supporting run uuids
  hub_pack_slug  VARCHAR(120)                        set on PULLED_FROM_HUB only
  metadata_json  JSONB NOT NULL DEFAULT '{}'

  UNIQUE (parent_pack_id, child_pack_id, edge_kind)
  INDEX  (workspace_id, child_pack_id)
  INDEX  (workspace_id, parent_pack_id)
  INDEX  (edge_kind)
```

The unique constraint + `SkillLineageEdgeRepository.upsert_edge` give
"insert-once, merge-after" semantics: re-recording the same triple
folds new run ids into the existing JSONB array (set semantics) instead
of throwing an integrity error.

Routes:

| Method | Path                                       | RBAC   | Rate                          |
|--------|--------------------------------------------|--------|-------------------------------|
| GET    | `/skills/packs/{pack_id}/graph?depth=N`    | member | `skill_graph_read 30/60s`     |
| GET    | `/skills/packs/{pack_id}/lineage`          | member | `skill_lineage_read 30/60s`   |

`depth` is bounded server-side; `> MAX_DEPTH=3` → 422. BFS walks
undirectionally with `MAX_NODES=200`; past the node cap, response sets
`truncated=true`.

Three isolation guarantees: focus lookup rejects cross-workspace 404;
every BFS expansion is scoped to the caller workspace; hub-pull edges
carry only `hub_pack_slug` (no FK to source workspace pack).

Edge-write helpers ([`backend/app/services/skill_graph.py`](../backend/app/services/skill_graph.py)):
`record_lineage_edge_for_propose`, `record_lineage_edge_for_pull`,
`record_lineage_edge_for_supersede`. All idempotent; merge run ids.

Audit: `skill_graph.queried` (depth==MAX), `skill_lineage.edge_created`,
`skill_lineage.edge_backfilled` (migration 0058 replay, log-only —
audit table may not exist on a fresh deploy).

Frontend renders at `/skills/[packId]/graph` via `@xyflow/react`. Edge
colours encode kind: `derived_from` blue, `supersedes` fuchsia,
`forked_from` amber, `pulled_from_hub` emerald. Hub nodes are inert
placeholders.
