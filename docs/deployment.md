# Deployment

Operator reference for running SenHarness in production. For a 10-minute
local try-it-out see [quickstart.md](quickstart.md).

## Decision flow

| Situation                                                                | Recommended path                                                                              |
|--------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
| Single server, one domain, Let's Encrypt is fine                          | `docker-compose.prod.yml` + Traefik (built-in path below)                                     |
| Org already runs Traefik / nginx-ingress / Kong                          | Use the same app + db + redis + worker + scheduler services; skip the Traefik service; point existing proxy at the backend container |
| Kubernetes                                                                | V3 roadmap — `kubernetes/` directory has a manifest starter, expect customisation             |
| Air-gapped                                                                | Mirror all Docker images; build from source; supply a private LLM endpoint (Ollama / vLLM / SGLang) |

## Production docker-compose

```bash
# 1. Generate strong secrets and populate .env (openssl / python helpers in quickstart.md).
cp .env.example .env

# 2. Export per-host variables (compose references them with `${VAR:?...}` — missing aborts start).
export DOMAIN=agents.example.com
export ACME_EMAIL=ops@example.com

# 3. Boot — both compose files needed; prod file is an override, not a replacement.
docker compose \
    -f docker-compose.yml \
    -f docker-compose.prod.yml \
    up -d
```

What the prod override adds:

* **Network isolation** — `backend-internal` network (db, redis, backend,
  worker, scheduler, frontend) is `internal: true`. Traefik bridges
  between `edge` and `backend-internal` for HTTP routes only.
* **Redis password required** — `--requirepass ${REDIS_PASSWORD:?...}`.
  Compose refuses to start if empty.
* **Resource limits** — every service gets CPU + memory ceiling; data
  tier (db, redis) reserves a floor so an overloaded app tier can't
  starve it.
* **Traefik security headers** — `sh-secure` middleware applies HSTS,
  frameDeny, XSS-block, content-type-nosniff, referrer-policy to every
  public router. App-level `SecurityHeadersMiddleware` sets the same
  headers as defence in depth.
* **Healthchecks target `/readyz`** — Traefik only routes traffic after
  backend reports `{"status":"ready"}` (DB probe + Redis probe both
  pass).

## Agent sandbox

The biggest security knob. Default is safe; changes are opt-in and
logged.

### Defaults

* `execute` defaults to **false** — agents can read / write their
  scratch dir, but not run arbitrary shell.
* Permission ruleset defaults to `default` (not `permissive`).
* Every `execute` / destructive filesystem call goes through the HITL
  approval queue.
* In `APP_ENV=production`, the combination `sandbox.kind = "local"` +
  `execute: true` is **refused at startup** unless you explicitly set
  `SANDBOX_LOCAL_EXECUTE_PROD=true`. This prevents a misconfigured
  agent from running shell commands *inside the SenHarness backend
  container* — which is equivalent to backend compromise.

### Sandbox kinds

| Kind                                       | What it does                                              | When to use                                                  |
|--------------------------------------------|-----------------------------------------------------------|--------------------------------------------------------------|
| `state`                                    | In-memory state, no filesystem, no shell.                 | Pure-Python agents (text generation, summarisation).         |
| `local`                                    | Filesystem scoped to a per-session dir; shell off.        | Agents that read/write user-supplied files; safest non-code default. |
| `local` + `execute=true` (**prod-gated**)  | Shell in the backend container.                            | Not recommended. Requires explicit `SANDBOX_LOCAL_EXECUTE_PROD=true`. |
| `docker`                                   | Per-session ephemeral container via `DockerSandbox`.       | Code-running agents. Requires Docker daemon access.          |
| `docker` + rootless podman / sysbox / gVisor | Same as above, sandbox container can't escape to host root. | Public-facing deployments running untrusted code.       |
| `ssh` (M2.5.10, opt-in)                    | Remote-execution backend; per-command HITL approval.       | Workspace-owned hosts already accessed via SSH.              |

### Docker socket mounts

Mounting `/var/run/docker.sock` into the backend container is equivalent
to giving that container host root. SenHarness requires it only when
an agent uses `sandbox.kind = "docker"`. For production:

1. **Preferred**: rootless Docker / podman / sysbox-runc so the daemon
   doesn't run as root in the first place.
2. **Alternative**: dedicated dockerd-in-dockerd (DinD) service +
   point the backend at `DOCKER_HOST=tcp://dind:2376` with mTLS.
3. **Last resort**: accept the tradeoff, audit agent configs carefully,
   pin the backend image to a known-clean version.

### SSH backend (opt-in)

`pip install ".[ssh-sandbox]"`. **Default posture is fail-closed at four
independent layers**: the dependency is opt-in, the platform admin must
explicitly enable it, the private key must live in the vault, the host
key is pinned, every command goes through the approval queue.

Threat model:

1. **Credential exfiltration.** Key resolves through the same envelope-
   encryption path as every other vault item; nothing else in the
   codebase reads it.
2. **Lateral movement.** A compromised agent (prompt injection, captured
   channel) running an SSH command gets the same blast radius as
   anything else with the workspace's key. Approvals on every command +
   an allowlist in production keep this gated.

Enable:

```
PUT /api/v1/admin/platform-settings/security.sandbox
{ "allow_ssh_backend": true, "confirmed_dangerous": true }
```

`security.sandbox.allow_ssh_backend` is a dangerous-change setting — M0.13
admin UI requires explicit confirmation. Workspace agents that wire
`kind=ssh` while the switch is off get `SandboxKindDisabled` +
`sandbox.ssh_kind_disabled` audit so platform admin sees attempted use.

Store the private key in the workspace vault, then configure the
agent's `metadata.sandbox`:

```yaml
sandbox:
  kind: ssh
  ssh:
    host: ops-bastion.example.com
    port: 22
    user: deploy
    private_key_ref: vault://workspace/ops_ed25519
    known_hosts_pin: |
      ops-bastion.example.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA...
    execute: true
    require_approval: true
    command_allowlist: [uptime, df, systemctl]
    connect_timeout_seconds: 30
    command_timeout_seconds: 60
```

Config reference:

| Field                     | Type                    | Default  | Notes                                                              |
|---------------------------|-------------------------|----------|--------------------------------------------------------------------|
| `host`                    | string (1-200)          | required | DNS name or IP                                                     |
| `port`                    | int (1-65535)           | 22       |                                                                    |
| `user`                    | string (1-80)           | required |                                                                    |
| `private_key_ref`         | `vault://workspace/<key>` | required | Plaintext keys rejected at parse time                              |
| `known_hosts_pin`         | OpenSSH known_hosts line | required | Mismatch = `sandbox.ssh_known_hosts_mismatch`                      |
| `execute`                 | bool                    | `false`  | Arbitrary shell. Default off.                                      |
| `require_approval`        | bool                    | `true`   | Per-command HITL gate                                              |
| `command_allowlist`       | list[string]            | `[]`     | First-token match. Empty = open in dev, blocked in prod            |
| `connect_timeout_seconds` | int (1-120)             | 30       |                                                                    |
| `command_timeout_seconds` | int (1-600)             | 60       |                                                                    |

`execute=True` + `command_allowlist=[]` is **rejected at build time** in
production (`SshConfigInvalid`, code `sandbox.ssh_config_invalid`,
`extras.reason='production_requires_allowlist'`). Operators who want
shell access in production must commit to a finite list of allowed
first tokens.

Approval flow for each `run_command`:

1. Validate command against `command_allowlist` (first-token match,
   `shlex.split`). Rejected → `SshCommandRejected` +
   `sandbox.ssh_command_rejected` audit; no network activity.
2. Persist `approvals` row with `resource_type=NULL`,
   `tool_name='ssh_execute'`, `tool_args={host, user, command}`,
   `expires_at=now+5min`.
3. Poll row every 1 s for terminal status. Polls through standard
   `POST /api/v1/approvals/{id}/decision` so existing approval card in
   chat UI works unchanged.
4. On `APPROVED`: open `asyncssh` connection (lazy — first call only),
   run command, audit `sandbox.ssh_command_executed` with
   `host / user / exit_code / duration_ms / approval_id`.
5. On `DENIED` / `EXPIRED`: `SshCommandDenied` with approval id and
   final status in `extras`.

Connection is **not pooled** — each command opens a fresh
`SSHClientConnection` and tears it down on exit so the audit trail keeps
clean 1:1 between approvals and live sessions. Pooling can land in M3
if benchmarks justify the extra failure modes.

`stdout` / `stderr` capped at 4 KB each per command; oversize gets
`...[truncated]` suffix in result and audit metadata. Operators who need
full output should pipe to a remote file and `read_file` it — SFTP read
path doesn't truncate.

Workspace ids and `vault://` refs are SHA-256-prefixed (16 hex chars)
in audit so raw values never land in log files.

Audit: `sandbox.ssh_kind_disabled`, `vault.private_key_resolved`,
`sandbox.ssh_session_opened`, `sandbox.ssh_command_executed`,
`sandbox.ssh_command_rejected`, `sandbox.ssh_known_hosts_mismatch`.

## Agent reliability guards (L6)

Source of truth:
[`backend/app/agents/harness/reliability.py`](../backend/app/agents/harness/reliability.py).

A SenHarness Agent run is a long-running, partially-observable process.
Even a strong model will occasionally loop, call the same tool forever,
blow past the iteration budget, or return megabyte-sized tool outputs.
The reliability layer is the set of guards that keep these failure
modes from reaching the end user.

Policy-gated per agent via `metadata.reliability`. Defaults safe; turn
individual guards off only when their behaviour conflicts with the
agent's design (e.g. polling loops legitimately repeat tool calls).

| # | Guard                      | What it prevents                                    | Default |
|---|----------------------------|-----------------------------------------------------|---------|
| 1 | `stuck_loop_detect`        | Same `(tool_name, args)` repeated forever           | **on**  |
| 2 | `tool_error_recovery`      | Transient tool errors breaking the whole run        | **on**  |
| 3 | `orphan_repair`            | `ToolCallPart` with no matching `ToolReturnPart`    | **on**  |
| 4 | `adaptive_reasoning`       | Wasting high-effort reasoning on trivial asks       | off     |
| 5 | `limit_warnings`           | Silently running out of iteration budget            | **on**  |
| 6 | `tool_output_overflow`     | Blowing the context window on one huge tool result  | **on**  |
| 7 | `system_reminders`         | Long runs drifting from the original instruction    | off     |

### Stuck-loop detection

Each `FunctionToolCallEvent` hashed into a signature
(`tool_name|sha1(args)`) and pushed into a rolling window. When the
most-common signature hits `stuck_loop_threshold` within
`stuck_loop_window`, the runner injects a `THINKING` event telling the
model to change approach. Visible in chat UI + trace-replay.

Tunables: `stuck_loop_window (6)` · `stuck_loop_threshold (3)`.

### Tool error recovery

`ReliabilityState.should_retry_tool(tool)` tracks per-tool per-run retry
budget (`tool_error_max_retries`, default 2). The runner sleeps
`tool_error_backoff_base_ms · 2^(attempts-1)` before pydantic-ai
retries. Successive hits escalate to the model as a tool error message.

Tunables: `tool_error_max_retries (2)` · `tool_error_backoff_base_ms (250)`.

### Orphan repair

pydantic-ai rejects history with a `ToolCallPart` whose `tool_call_id`
has no matching `ToolReturnPart` ("orphan"). Happens naturally when a
previous run was cancelled mid-tool-call. `repair_orphan_tool_calls()`
walks the message history once, drops orphan parts (and any response
message containing *only* orphans), and logs how many were removed.

### Adaptive reasoning effort

When `adaptive_reasoning: true`, `pick_reasoning_effort()` maps the user
prompt to `low | medium | high` and writes it to the agent's
`model_settings`:

| Signal                                                          | Effort |
|-----------------------------------------------------------------|--------|
| Prompt contains `plan` / `think` / `analyze` / `design` / etc.  | high   |
| Prompt length ≥ `adaptive_reasoning_high_threshold` (800)       | high   |
| Prompt length ≤ `adaptive_reasoning_low_threshold` (120)        | low    |
| otherwise                                                       | medium |

### Limit warnings

Runner calls `tick_iteration()` between graph nodes. When
`iteration_count / iteration_budget` crosses `limit_warning_ratio`
(default 80%) a `THINKING` event is emitted **once** giving the model
one last chance to wind down cleanly instead of hitting hard cap.

### Tool output overflow

`truncate_tool_output()` serialises the tool result. If it exceeds
`tool_output_max_chars` (default 4000):

1. Truncate the value to `max_chars`.
2. Write **full** payload to
   `{STORAGE_LOCAL_PATH}/scratch/{session_id}/tool_output_{call_id}.json`.
3. Set `truncated: true` in `RunEvent.TOOL_RESULT` payload.
4. Append one-line hint pointing at on-disk dump.

User can open the scratch file from trace-replay to inspect.

### System reminders (opt-in)

When enabled, runner injects short plain-text reminder every
`system_reminder_every_iters` iterations so the model's effective
context never drifts past the original instruction. Worth its token
cost only on 20+ turn sessions.

### Configuration

```json
{
  "reliability": {
    "stuck_loop_detect": true,
    "stuck_loop_window": 8,
    "stuck_loop_threshold": 4,
    "tool_error_recovery": true,
    "tool_error_max_retries": 3,
    "adaptive_reasoning": true,
    "limit_warning_ratio": 0.75,
    "tool_output_max_chars": 8000,
    "system_reminders": false
  }
}
```

Misspelled flags are silently ignored (no crash, no surprise) so
operators can roll forward safely.

## Scheduler high availability

`docker-compose.prod.yml` ships `replicas: 1` for the scheduler.
Leader-election primitive
([`backend/app/workflows/leader.py`](../backend/app/workflows/leader.py))
uses Redis `SET NX EX` lease with CAS-based renewal + Lua-safe release,
so scaling to `replicas: N > 1` is **safe** (extras poll as hot standby)
but wasteful — cron only fires from the lease holder.

Set `replicas: 2` only if you have an SLA requiring single-minute
failover for cron jobs. Single-replica restart takes ~10 s to re-acquire
the lease after a crash.

## Keyring provider choice

`KEYRING_PROVIDER` picks how the Vault's KEK is wrapped:

| Provider                                       | Best for                 | Setup                                                                          |
|------------------------------------------------|--------------------------|--------------------------------------------------------------------------------|
| `env` (default)                                | Dev + small self-hosted  | Set `SENHARNESS_MASTER_KEY` in `.env` (persist it — auto-generated keys lost on restart) |
| `file`                                         | Mid-size self-hosted     | JWKS at `KEYRING_FILE_PATH`, `chmod 600`, back up separately                   |
| `passphrase`                                   | High-security self-hosted| Interactive stdin passphrase at boot; argon2id hashing                         |
| `aws_kms` / `gcp_kms` / `azure_kv` / `vault`   | Cloud prod               | Configure the provider and its credentials                                     |

When rotating providers: see
[`backend/app/security/keyring/`](../backend/app/security/keyring/) —
the rotation path re-wraps DEK columns, ciphertext is unchanged.

## Observability

Enable at least one of:

* **Logfire** (`LOGFIRE_TOKEN`) — pydantic.dev-hosted, zero setup,
  richest agent traces.
* **OpenTelemetry OTLP** (`OTEL_EXPORTER_OTLP_ENDPOINT`) — Grafana Tempo,
  Honeycomb, self-hosted Jaeger.
* **Langfuse** (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`) — LLM-
  specific traces and costs.
* **Sentry** (`SENTRY_DSN`) — error tracking, crash reports.

SenHarness auto-instruments FastAPI + SQLAlchemy; turn any of the above
on and metrics just flow. Prometheus scrape at
`/api/v1/metrics/prometheus`. See
[extensions-and-governance.md#evaluation-l5](extensions-and-governance.md#evaluation-l5)
for the counter / histogram catalog.

## Backup

Minimum viable production backup:

```bash
# Daily Postgres dump — stream to S3, NAS, etc.
docker compose exec -T db pg_dump \
    -U $DB_USER -d $DB_NAME | gzip > backup-$(date +%F).sql.gz

# Master key + keyring file (if using file provider) — back up to a
# SEPARATE location from the DB so a single compromise doesn't give
# the attacker both the ciphertext and the key.
cp .env /secure/offline/backup/.env-$(date +%F)
```

Restore:

```bash
gunzip < backup-2026-04-23.sql.gz | \
    docker compose exec -T db psql -U $DB_USER -d $DB_NAME
```

V3 will ship a helper script; for V1 the above + a cron job is enough
for most companies.

## Updating in place

```bash
cd SenHarness
git fetch --all && git checkout v0.2.0           # or whatever tag
docker compose \
    -f docker-compose.yml \
    -f docker-compose.prod.yml \
    up -d --build
```

Backend entrypoint runs migrations automatically on boot; alembic is
advisory-locked so concurrent upgrades from multiple backend replicas
are safe.

## Rollback

```bash
git checkout <previous-tag>
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

If the previous version had an older schema, Alembic blocks startup
with a clear error. Restore from a DB dump taken before the upgrade.

## Hard don'ts

1. **Don't run `APP_ENV=production` with any of `JWT_SECRET_KEY` /
   `DB_PASSWORD` / `REDIS_PASSWORD` / `SENHARNESS_MASTER_KEY` at
   their dev defaults.** SenHarness refuses to start in that
   combination.
2. **Don't mount `/var/run/docker.sock` into a public-facing backend
   container without rootless / sysbox / DinD.** It is host-root by
   another name.
3. **Don't flip `security.sandbox.allow_ssh_backend=true` without an
   allowlist** — production rejects `execute=true` + empty allowlist at
   build time.
4. **Don't run the worker without the scheduler** — every recurring job
   (judge, curator, verifier, evolver, retention, agent-profile,
   user-profile, insights) is scheduled, not webhook-driven.
5. **Don't share `SENHARNESS_MASTER_KEY` across deployments.** A leaked
   key compromises every secret in every workspace under that key.
6. **Don't disable email verification (`auth_require_email_verification=false`)
   in a multi-tenant prod deployment** — it makes account-takeover
   trivial via password reset on a registered-but-unverified address.
