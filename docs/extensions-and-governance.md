# Extensions & Governance

Reference for the pluggable surfaces (agent runtime adapters, channel
providers, MCP servers, KB connectors, plugins, protocol gateways) plus
the governance layers (approvals, notifications, evaluation, retention,
platform settings, profiles, quota, registration, provider routing).

```
External                                Workspace                                    Platform
─────────                               ─────────                                    ─────────
IM platforms ─► channel providers       agent_profile / user_profile                 platform_settings
MCP servers  ─► mcp_servers              workspace_quota                              keyring + crypto
LLM upstreams ─► provider catalog       sender_allowlist / shields                   retention cascade
Hub / KB     ─► kb_connectors            served_alias_map / failover_chain           notification registry
Plugins      ─► plugin_host (signed)    eval / judge / reflection / approval         email transport
Frameworks   ─► protocol gateway         audit_events                                 plugin signing root
```

Every surface follows the same pattern: one file, one class, one
`register_*` call. No schema migrations for adding a new provider /
adapter / connector / plugin.

---

## Agent Runtime adapters

`AgentBackend` ([`backend/app/agents/kernels/base.py`](../backend/app/agents/kernels/base.py))
is the seam between the platform (sessions / policies / memory / audit /
UI) and the actual engine that runs the model.

```python
class AgentBackend(Protocol):
    backend_kind: str

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]: ...
    async def cancel(self, run_id: uuid.UUID) -> None: ...
    def capabilities(self) -> BackendCapabilities: ...
```

Three methods. `run` yields `RunEvent`s, `cancel` marks an in-flight run
for abort, `capabilities` is a pure descriptor the UI reads once.

Officially supported backends:
[`app/agents/kernels/native/`](../backend/app/agents/kernels/native/) and
[`app/agents/kernels/openclaw/`](../backend/app/agents/kernels/openclaw/).
**Never call `pydantic_ai.Agent` directly inside a run** — go through
`AgentBackend`.

### `RunRequest` payload

| Field                                                          | Why you might need it                                 |
|----------------------------------------------------------------|-------------------------------------------------------|
| `user_text`                                                    | The message the user just typed.                      |
| `message_history`                                              | Prior turns as OpenAI-style dicts.                    |
| `attachments`                                                  | Files the user uploaded (base64 or ref).              |
| `toolbox`                                                      | Tool names this agent is allowed to call.             |
| `skills`                                                       | Active skill-pack names.                              |
| `policy`                                                       | Autonomy level, sandbox spec, shields, approval rules.|
| `iteration_budget`                                             | How many tool-call loops you may do.                  |
| `model_override`                                               | User-selected model for this run, or None.            |
| `workspace_id` / `agent_id` / `session_id` / `identity_id`     | Tenancy + audit context.                              |

### `RunEvent` vocabulary

| Kind            | When to emit                                                                                  |
|-----------------|-----------------------------------------------------------------------------------------------|
| `delta`         | Streaming text chunk.                                                                         |
| `thinking`      | Model reasoning (shown in the "thinking" accordion).                                          |
| `tool_call`     | `{"id", "name", "args"}`                                                                      |
| `tool_result`   | `{"id", "result", "truncated"}` (plus `media` for MCP MEDIA results, see MCP).                |
| `usage`         | `{"input_tokens", "output_tokens", "cost_usd"}` at end.                                       |
| `error`         | `{"code", "message", "retryable"}` — prefix `code` with `backend_kind` for greppable logs.   |
| `final`         | `{"message_id", "summary"}` — **must** be the last event.                                     |

### `BackendCapabilities`

```python
BackendCapabilities(
    display_name="…",
    description="One sentence users see when picking a runtime.",
    docs_url="https://…",
    requires_adapter=False,         # True for remote backends
    supports_streaming=True,
    supports_parallel_tools=True,
    supports_thinking=False,
    supports_native_mcp=False,
    supports_vision=False,
    max_context_tokens=32_000,
    notes="Free-form troubleshooting hints.",
)
```

Once registered + imported, `GET /api/v1/agents/runtimes` picks it up
automatically. No schema edits, no migration.

### Tenancy + isolation

`run()` receives `workspace_id` and `identity_id`. Two rules:

1. **Never let a tool call cross tenants.** If the runtime stores state
   (cache, memory, embeddings) key it by `workspace_id`.
2. **Never include one tenant's secrets in another tenant's prompt.**
   The built-in shields guard injects context per session; don't bypass
   them by reading Vault rows directly.

See [adding-features.md](adding-features.md#agent-runtime-adapter) for a
minimal 50-line `echo` adapter walkthrough.

---

## Channel providers

`ChannelProvider` bridges external IM platforms (Slack, Feishu, DingTalk,
WeCom, Discord, Teams, Telegram, QQ, WeChat, generic webhook) to agents
inside a workspace. One file, one class, one
`register_provider(...)` call.

```python
class ChannelProvider:
    kind: str                              # unique lowercase identifier

    @classmethod
    def metadata(cls) -> ChannelProviderMeta: ...

    def verify_signature(self, *, channel_config, headers, body) -> None: ...
    def parse_inbound(self, payload, headers) -> InboundMessage | None: ...
    def handshake_response(self, payload) -> dict | None: ...
    async def post_reply(self, *, channel_config, thread_key, text) -> None: ...
```

Five methods. All optional to override except `parse_inbound`. Default
`verify_signature` trusts the `?token=` query shared secret the ingress
route already checked.

`metadata()` drives the Channel-create form:
* `required_config_fields` / `optional_config_fields` render one input
  per entry.
* Names containing `secret`, `token`, `password`, or `key` are
  auto-masked with reveal button.

### Stream mode

When SenHarness sits behind NAT / on a laptop / in a private subnet, the
IM platform can't call inbound. Stream mode flips the direction:
SenHarness opens an outbound link to the IM platform and listens.

| Provider           | Stream transport                  | Default |
|--------------------|-----------------------------------|---------|
| `feishu` / `lark`  | `lark-oapi` WebSocket             | stream  |
| `dingtalk`         | `dingtalk-stream` (Stream Mode)   | stream  |
| `wecom`            | `wecom-aibot-sdk-python` WSS      | stream  |
| `discord`          | `discord.py` Gateway              | stream  |
| `qq`               | `qq-botpy` Gateway                | stream  |
| `wechat`           | iLink HTTP long-poll (no extra)   | stream  |
| `slack` / `teams` / `telegram` | — (webhook-only)      | —       |

Provider-side:

```python
class FooProvider(ChannelProvider):
    @classmethod
    def supports_stream(cls) -> bool: return True

    @classmethod
    def stream_available(cls) -> bool:
        try: import some_optional_sdk  # noqa: F401
        except ImportError: return False
        return True

    async def run_stream(self, *, channel, dispatch, stop):
        from app.services.channels._foo_stream import run_loop
        await run_loop(channel=channel, dispatch=dispatch, stop=stop)
```

The runtime gives back `channel` (with `_plain_config` stamped on),
`dispatch(InboundMessage)` (lands the message on the same agent-run
path the webhook ingress uses), and `stop` (`asyncio.Event` to await
until set; raise unrecoverable errors so the runtime applies exponential
backoff).

Stream SDKs are gated behind `pip install ".[channels-stream]"`. Each
provider's `stream_available()` does a try-import and reports back;
frontend greys out the stream toggle and prints the install hint when
the extra isn't installed.

Lifecycle: `CHANNEL_RUNTIME_INPROCESS=true` (default) runs the supervisor
inside FastAPI lifespan; in multi-worker production set to `false` and
run `python -m cli.commands channels run`. Runtime auto-restarts a
channel on enable / mode / config change (CRUD routes call
`notify_runtime_restart`). `GET /api/v1/channels/{id}/status` returns
`{connected, last_event_at, last_error, started_at, mode}` — frontend
polls every 5 s.

### Channel security (M0.8)

Eight hardenings of the IM channel surface:

| #   | Hardening                                                          | Default              | Override                            |
|-----|--------------------------------------------------------------------|----------------------|-------------------------------------|
| 8.1 | Generic webhook HMAC signature mandatory                            | on                   | `verify_signatures: false` per chan |
| 8.2 | Discord guild allowlist + DM block                                  | `allow_dms=false`    | `allowed_guild_ids` / `allow_dms`   |
| 8.3 | Slack `expected_team_id` pinning                                    | unset                | `expected_team_id` per channel      |
| 8.4 | Sender allowlist (`allow_all` / `allow_listed` / `deny_listed`)     | `allow_all`          | per channel                         |
| 8.5 | Shields default-on (PII log, secret redaction, prompt-injection)    | on for new agents    | `policy.shields = []` to opt out    |
| 8.6 | Keyring open uses fd-first read + `keyring.opened` audit            | on                   | not configurable                    |
| 8.7 | Per-sender (20/min) + per-channel (200/min) inbound rate limit      | on                   | not configurable                    |
| 8.8 | Same external bot/app cannot bind two channels                      | on                   | delete conflicting row              |

Config shapes:

```jsonc
// Generic webhook (kind=webhook)
{"verify_signatures": true, "hmac_secret": "<random>"}
// Inbound: X-HMAC-Signature: <hex(hmac_sha256(secret, body))>
// Also accepts sha256=<hex> prefix for GitHub-style clients.

// Discord (kind=discord)
{"bot_token": "…", "public_key": "…",
 "allowed_guild_ids": ["123…"], "allow_dms": false}

// Slack (kind=slack) — expected_team_id optional
{"bot_token": "xoxb-…", "signing_secret": "…", "expected_team_id": "T01234567"}

// Sender allowlist (any kind)
// On Channel.sender_allowlist_json:
{"mode": "allow_all"}                            // default
{"mode": "allow_listed", "allow": ["U01234"]}
{"mode": "deny_listed", "deny": ["U_BAD"]}
```

Migration `0035_channel_security_pack` adds `sender_allowlist_json={}`
and computes `external_app_id_hash` for every row. Two rows sharing the
same bot/app keep `external_app_id_hash=NULL` and emit
`channel.dup_external_app_at_migration` audit; operator resolves manually.

Rate limit semantics (Redis fixed-window, fail-open on Redis down):

* `channel:<id>:sender:<external_user_id>` — 20 req / 60 s.
* `channel:<id>:total` — 200 req / 60 s.

Anonymous senders share one bucket per channel under `sender_id="anonymous"`.

Audit: `channel.signature_required_but_unset`, `channel.signature_failed`,
`channel.discord_filtered`, `channel.slack_team_mismatch`,
`channel.sender_blocked`, `channel.sender_filter_unknown_mode`,
`channel.rate_limited` (`limit_kind` is `per_sender` or `per_channel`),
`channel.dup_external_app_at_migration`.

Keyring audit: every provider emits one `keyring.opened` line on
construction:

```
keyring.opened provider=<env|file|vault|aws_kms|gcp_kms|azure_kv|passphrase> key_ref_hash=<sha256/16-hex>
```

The `key_ref` itself (path, ARN, transit key name) is **not** logged
— only its SHA-256 prefix. Unwrap failures raise `KeyringAccessError`
without exposing the underlying provider exception or filesystem path.

**Windows note.** `FileKeyring._load` enforces POSIX `0600` on
Linux/macOS. Windows degrades to a single WARNING log line; configure
NTFS ACLs externally so only the SenHarness service account can read
the keyring file.

---

## MCP transports + OAuth

`pip install ".[mcp]"`. Deployments without MCP servers keep working
without the extra; catalogue endpoints raise `mcp.sdk_unavailable` (503).

Transports:

| Transport         | When to use                                                                            | Required fields                          |
|-------------------|----------------------------------------------------------------------------------------|------------------------------------------|
| `stdio`           | Local development, hermetic CI. Spawns the MCP process and pipes JSON-RPC.             | `command`, optional `args_json` / `env`  |
| `sse`             | Legacy MCP servers exposing only `text/event-stream`.                                  | `url` (full SSE endpoint)                |
| `streamable_http` | Modern bidi HTTP-with-streaming — canonical for new MCP deployments.                   | `url` (single endpoint)                  |

Migration `0052_mcp_transports_oauth` normalises pre-M2.5.4 `transport='http'`
rows to `streamable_http`. Legacy `endpoint` column preserved as
back-compat health-ping URL.

Schema additions to `mcp_servers`:

```
├─ transport      VARCHAR(40) NOT NULL DEFAULT 'stdio'
├─ url            VARCHAR(500)        — set for sse / streamable_http
├─ endpoint       VARCHAR(1024)       — legacy health-ping URL
├─ command        VARCHAR(512)        — set for stdio
├─ args_json      JSONB
├─ env_json       JSONB
└─ auth_json      JSONB               — { type: "oauth" | "bearer", … }
```

### OAuth client-credentials flow

```
McpServerCreate.auth_oauth = {…}  → route immediately seals client_secret into workspace vault
                                    under mcp.oauth_secret.<slug>; rewrites
                                    auth_json.client_secret_ref to ${vault://…}
                                                ↓
                                      get_valid_token():
                                         1. cache hit → reuse
                                         2. expired   → refresh
                                         3. failed    → fresh dance
                                                ↓
                                      McpClient.connect(url, headers={Bearer …})
```

Plaintext `client_secret` is **only** accepted on the form so an operator
can paste the value once. Tokens themselves live in `mcp.oauth.<server_id>`
under `VaultItemKind.OAUTH` — JSON-encoded so we can rotate the
short-lived `access_token` without losing `refresh_token`.

### MEDIA result type

When a server returns image / audio / file parts, M2.5.4 client surfaces
them in `McpToolResult.media`:

```python
result = await client.call_tool("screenshot", {})
# result.text == "screenshot taken"
# result.media == [McpMediaPart(kind="image", mime="image/png", data_b64="iVBORw==")]
```

`build_mcp_tool_result_event` translates into the `RunEvent` wire shape:

```python
event.data = {
    "id": call_id,
    "name": "screenshot",
    "result": "screenshot taken",
    "is_error": False,
    "media": [{"type": "image", "mime": "image/png", "data": "iVBORw=="}],
}
```

`media` key is always present — frontend doesn't have to defend against
absence for the legacy text-only path.

### Operational guard rails

| Guard                      | Default     | Behaviour                                                                                      |
|----------------------------|-------------|------------------------------------------------------------------------------------------------|
| Keepalive ping             | 30 s        | Two consecutive failures → `mcp.keepalive_timeout` + close transport. Next `call_tool` raises. |
| Hard cancel                | 5 min       | `asyncio.wait_for(timeout=request_timeout_seconds)`. A hung MCP server can't wedge a chat turn. |
| Per-server concurrency cap | 4           | `Semaphore(max_concurrent)`. 5th call waits FIFO; `mcp.concurrency_limit_hit` audit when wait matters. |

Raising the cap above 4 needs explicit operator acknowledgement via
`metadata_json.max_concurrent` — most real-world MCP backends impose
their own per-tenant rate limits.

Audit: `mcp.client_connected`, `mcp.tool_called`, `mcp.keepalive_timeout`,
`mcp.oauth_token_acquired`, `mcp.oauth_token_refreshed`,
`mcp.oauth_failed`, `mcp.concurrency_limit_hit`. `access_token`,
`refresh_token`, `client_secret` stripped from every audit metadata
payload.

### Federation note (M3 preview)

Every MCP row is strictly workspace-scoped. M3 federation will add
server-share rows (one MCP target → many workspaces, no copying OAuth
secret) and a `served_alias_map` analogue for tool names. Both designs
ride on top of the M2.5.4 client without modifying it.

---

## Knowledge-base connectors

SenHarness knowledge collections ingest documents through connectors:
URL, file upload, S3 (+ MinIO / R2 / OSS), and custom sources. One
class, one registration — no migration needed.

Tables:

| Table             | Role                                                              |
|-------------------|-------------------------------------------------------------------|
| `kb_sources`      | One row per connector instance attached to a collection.          |
| `kb_source_syncs` | Per-run progress: counters, events transcript, status.            |
| `kb_access`       | Document-level ACL (identity / department / workspace principals).|

`kb_sources.kind` is `String(32)` so extra connector kinds need no
Alembic migration.

### Built-in connectors

**`url`** — fetch a single public HTTP(S) page. URL run through
`app.core.url_safety.assert_safe_url` (refuses metadata endpoints,
private networks, non-HTTP schemes).

```json
{"url": "https://example.com/handbook/onboarding", "title": "Onboarding handbook"}
```

**`file`** — ingest existing attachments. Only emits pointers; text
extraction reuses `knowledge.ingest_attachment`.

```json
{"attachment_ids": ["<uuid>", "<uuid>"]}
```

**`s3`** — list every object under a bucket prefix. Works with MinIO /
R2 / OSS via `endpoint_url`. Requires `boto3`; missing → friendly error
event instead of crash.

```json
{
  "bucket": "knowledge-prod",
  "prefix": "handbook/",
  "endpoint_url": "https://minio.internal",
  "region_name": "cn-north-1",
  "access_key_id": "…",
  "secret_access_key": "…",
  "include_ext": [".md", ".txt"],
  "max_objects": 500
}
```

### Adding a custom connector

```python
# app/services/kb_connectors/confluence.py
from app.services.kb_connectors.base import (
    ConnectorDocument, ConnectorMeta, KbConnector, SyncProgressEvent,
)
from app.services.kb_connectors import register_connector

class ConfluenceConnector(KbConnector):
    kind = "confluence"

    def metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            display_name="Confluence",
            description="Import a Confluence space.",
            config_schema={
                "required": ["base_url", "space_key", "token"],
                "properties": {
                    "base_url": {"type": "string"},
                    "space_key": {"type": "string"},
                    "token": {"type": "string", "format": "password"},
                },
            },
            supports_incremental=True,
        )

    async def sync(self, *, config):
        yield SyncProgressEvent(level="info", msg="fetching pages")
        # … paginate the Confluence API …
        yield ConnectorDocument(...)

register_connector(ConfluenceConnector())
```

Import the module once from app startup so `register_connector` fires.
The connector shows up in `GET /api/v1/kb/connectors`, the source-create
form's kind picker, and the `run_sync` orchestrator.

### SSE frame format

```
event: progress
data: {"kind":"progress","ts":"2026-04-24T10:21:18+00:00","level":"info","msg":"fetching s3://…"}
```

Events: `started`, `progress`, `doc`, `done`, `failed`. A `:keepalive`
comment every 15 s so intermediaries don't idle out.

### Document-level ACL

Default: every workspace member can retrieve every doc. Admins override
per-collection or per-document:

* `subject_kind=identity, subject_id=<identity_uuid>` — grant to one person.
* `subject_kind=department, subject_id=<dept_uuid>` — grant to department.
* `subject_kind=workspace, subject_id=<workspace_uuid>` — "everyone in
  workspace" (default without explicit ACL).

`doc_id` null = "every doc in the collection"; non-null narrows to a
single document. Grants are **additive** — any matching grant allows
retrieval. The search path runs
`kb_source.filter_accessible_doc_ids` before the pgvector query.

---

## Plugin host (M2.5.5 + M3.5 + M3.9)

In-process registry that lets operators extend the native runner with
code outside the SenHarness tree. **Default-deny**: shipping default is
`allow_user_plugins=False` and `signing_root_pubkey=null`.

Six lifecycle hooks the runner fires:

| Hook               | Fires when…                                                          | Payload (kwargs)                                                                            |
|--------------------|----------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| `on_session_start` | Runner accepts a chat turn and binds per-run context.                | `run_id`, `workspace_id`, `session_id`, `identity_id`, `agent_id`, `served_model`, `upstream_model`, `provider_kind` |
| `on_session_end`   | Runner finishes (success / error / cancel / failover).               | start payload + `final_outcome`                                                             |
| `pre_llm_call`     | Once per upstream model request, before pydantic-ai streams it.      | start payload + `iteration` (1-based)                                                       |
| `post_llm_call`    | After the same model request finishes streaming.                     | start payload + `iteration` + `text_chars`                                                  |
| `pre_tool_call`    | Once per `FunctionToolCallEvent` (parallel calls fire separately).   | start payload + `tool_name`, `tool_call_id`, `args`                                         |
| `post_tool_call`   | Once per `FunctionToolResultEvent` (matches call id).                | start payload + `tool_name`, `tool_call_id`, `result`, `truncated`, `ok`                    |

Every payload is positional-keyword: callbacks accept `**kwargs`. The
host never feeds return values back to the runner — return-value mutation
is reserved for a future signed-only `transform_*` family.

### Manifest

Plugin folder ships one of `plugin.yaml` / `.yml` / `.toml` / `.json`:

```yaml
name: echo_logger
version: 0.1.0
description: Logs every tool call to stdout for debugging.
capability_scopes:
  - pre_tool_call
  - post_tool_call
entry_module: echo_logger.entry
```

| Field                | Required | Notes                                                                                          |
|----------------------|----------|------------------------------------------------------------------------------------------------|
| `name`               | yes      | Display name used in audit metadata.                                                           |
| `version`            | yes      | Free-form semver — informational; promoted to signature input by M3.9.                         |
| `description`        | yes      | One-line operator-facing summary.                                                              |
| `capability_scopes`  | yes      | Subset of the six hook names. Refuses any `register_hook` outside this list.                   |
| `entry_module`       | yes      | Dotted path to module exposing `register(ctx)`. `<folder>.entry` and flat `entry` style work.  |

### `register(ctx)` contract

The loader imports `entry_module` and calls `register(ctx)` once per
startup. `ctx` exposes three methods:

* `ctx.register_hook(hook_name, callback)` — wires the callback in.
  Refuses unknown hooks AND hooks not declared in `capability_scopes`.
  Async and plain (`def`) callbacks both accepted; host bridges sync
  callbacks transparently.
* `ctx.register_channel_kind(kind, factory)` — installs a new channel
  provider (M3.5).
* `ctx.register_model_provider(kind, factory)` — installs a catalog
  entry (M3.5).
* `ctx.register_tool(name, args_model, runner)` — reserved for M4.

`register_channel_kind` and `register_model_provider` **refuse to
override a built-in kind** — plugin authors must pick a fresh `kind`
string. A `register()` that raises lands as `plugin.load_failed`; sibling
plugins still register.

### Failure & timeout safety

`PluginHost.fire(...)` guarantees:

1. Each registered callback runs under
   `asyncio.wait_for(callback, timeout=HOOK_TIMEOUT_SECONDS)` (1.0 s).
2. Timeout → `plugin.hook_timeout` audit. Raise → `plugin.hook_failed`
   audit; exception never reaches the runner.
3. One exploding callback doesn't block its siblings — every callback
   for the hook is attempted.
4. The audit write itself is wrapped in try/except so a broken audit
   pipeline can't break the runner.

These combine to: **`fire` never raises into the run path.**

### Trust pipeline (M3.5 + M3.9)

```
filesystem folder
        │
        ▼
discover_plugins()  ─► manifest parse + sha256 + register() resolve
        │
        ▼
PluginRegistry row  ─► upsert by (name, version, sha256)
        │
        ▼
evaluate_plugin_for_load()
        ├── allow_user_plugins=False         → "disabled"
        ├── pubkey absent + dev-mode off     → "no_trust_root"
        ├── pubkey set + signature missing   → "signature_missing"
        ├── ed25519 verify fails             → "signature_invalid"
        ├── PluginRegistry row absent        → "not_in_registry"
        ├── approved_by_platform_admin=False → "not_approved"
        └── all gates pass                   → "approved"
                │
                ▼
        register(ctx)  ─► installs hooks / channels / providers
                │
                ▼
        plugin.loaded audit + status=LOADED
```

Each reason is a stable audit code (`plugin.<reason>`) so dashboards
group on a finite vocabulary.

### Platform settings

| Field                            | Default | What it does                                                                                                      |
|----------------------------------|---------|-------------------------------------------------------------------------------------------------------------------|
| `allow_user_plugins`             | `False` | Master switch. Off → one `plugin.disabled_by_platform_setting` audit; loader never reads disk.                    |
| `allow_unapproved_plugins`       | `False` | Dev-mode escape. On → bypass signature + registry approval. Production must keep off.                             |
| `signing_root_pubkey`            | `null`  | Base64 of 32-byte ed25519 verify key. Required when dev-mode is off.                                              |
| `auto_reload_on_admin_approve`   | `True`  | Approve route re-runs the loader so plugin starts firing without a restart.                                       |

`allow_user_plugins` and `allow_unapproved_plugins` are flagged
dangerous-change fields: flipping surfaces a confirmation dialog, writes
a `platform_settings.dangerous_change` audit row, and emails every other
platform admin.

### Operator runbook

**1. Mint a trust root**

```bash
python -c '
import base64, nacl.signing
sk = nacl.signing.SigningKey.generate()
print("PUBKEY:", base64.b64encode(bytes(sk.verify_key)).decode())
print("SIGNING_KEY (keep offline!):", base64.b64encode(bytes(sk)).decode())
'
```

Paste public key into `/admin/settings/plugins → signing_root_pubkey`.
Store the signing key out-of-band — never commit / upload.

**2. Sign a plugin folder.** Signed message is lowercase hex SHA-256 of
every regular file under the plugin folder (excluding `*.sig` siblings):

```python
import base64, hashlib, pathlib, nacl.signing

def folder_sha256(folder: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.name.endswith(".sig"):
            continue
        rel = path.relative_to(folder).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(path.read_bytes())
        digest.update(b"\xff")
    return digest.hexdigest()

folder = pathlib.Path("./my_plugin")
sha = folder_sha256(folder)
sk = nacl.signing.SigningKey(base64.b64decode("YOUR_BASE64_SIGNING_KEY"))
sig = sk.sign(sha.encode("utf-8")).signature
(folder / "plugin.yaml.sig").write_text(base64.b64encode(sig).decode("ascii"))
```

Drop folder + `plugin.yaml.sig` under `STORAGE_LOCAL_PATH/plugins/`.

**3. Scan + approve:**

```
POST /api/v1/admin/plugins/scan
GET  /api/v1/admin/plugins
POST /api/v1/admin/plugins/{id}/approve
POST /api/v1/admin/plugins/{id}/reject  {"reason": "fails review"}
```

`approve` flips `approved_by_platform_admin=True` and (when
`auto_reload_on_admin_approve=True`) re-runs the loader. `status=REJECTED`
is sticky — a rescan with the same sha256 will not demote back to
`DISCOVERED`. Re-uploaded plugin with different sha256 → fresh row.

### Capability scopes

| Scope                      | What it lets the plugin call                                          |
|----------------------------|-----------------------------------------------------------------------|
| `pre_tool_call`            | `ctx.register_hook("pre_tool_call", cb)`                              |
| `post_tool_call`           | `ctx.register_hook("post_tool_call", cb)`                             |
| `pre_llm_call`             | `ctx.register_hook("pre_llm_call", cb)`                               |
| `post_llm_call`            | `ctx.register_hook("post_llm_call", cb)`                              |
| `on_session_start`         | `ctx.register_hook("on_session_start", cb)`                           |
| `on_session_end`           | `ctx.register_hook("on_session_end", cb)`                             |
| `register_channel`         | `ctx.register_channel_kind(kind, factory)`                            |
| `register_model_provider`  | `ctx.register_model_provider(kind, factory)`                          |
| `register_tool`            | `ctx.register_tool(name, args_model, runner)` — reserved for M4       |

A plugin declares any subset; registry diff lands in `plugin.loaded`
audit metadata.

PyNaCl ships as `pip install ".[plugin-signing]"`. Deployments that keep
`allow_user_plugins=False` (default) never import PyNaCl. Dev-mode also
skips the import — cold-path cost is zero for the majority.

---

## Protocol compatibility gateway

External agent frameworks (Claude Code, Codex, etc.) can treat a
SenHarness workspace as if it were Anthropic, OpenAI, or both — while
still honouring per-workspace provider routing, served model names,
audit, and rate limits.

| Path                                            | Protocol                                       | Notes                                                                                      |
|-------------------------------------------------|------------------------------------------------|--------------------------------------------------------------------------------------------|
| `GET  /api/v1/openai/v1/models`                 | OpenAI Chat / Anthropic / OpenAI Responses     | Served names only.                                                                          |
| `POST /api/v1/openai/v1/chat/completions`       | OpenAI Chat Completions                        | Routes to workspace's default agent (skill / memory / shields apply).                       |
| `POST /api/v1/openai/v1/messages`               | Anthropic Messages                             | Provider passthrough.                                                                       |
| `POST /api/v1/openai/v1/messages/count_tokens`  | Anthropic helper                               | `len(text) // 4` estimator.                                                                 |
| `POST /api/v1/openai/v1/responses`              | OpenAI Responses                               | Provider passthrough.                                                                       |

Lives at
[`backend/app/api/v1/openai_compat.py`](../backend/app/api/v1/openai_compat.py)
(routes/auth/audit/rate limit),
[`backend/app/services/protocol_adapter.py`](../backend/app/services/protocol_adapter.py)
(pure protocol ↔ internal translation + SSE encoders),
[`backend/app/agents/kernels/protocol_kernel.py`](../backend/app/agents/kernels/protocol_kernel.py)
(provider passthrough kernel).

**Auth + scoping.** Every endpoint requires SenHarness JWT + active
workspace. Missing workspace → 401 `auth.no_active_workspace`.
Cross-workspace rejected. BYO-key **not supported** — upstream provider
key is read from the workspace's vault-backed providers.

**Provider passthrough mode** (Messages / Responses endpoints
intentionally bypass harness layers — skills / memory / shields /
sandbox / todos / plugins):

1. External frameworks bring their own context + tool catalogue. Re-
   injecting SenHarness skills would contaminate or break tool_use
   fidelity.
2. Protocol stream returns `tool_use` / `function_call` blocks; client
   runs them.
3. Keeping the gateway thin makes upstream provider behaviour
   reproducible — operators can debug "what would Claude have said"
   without fighting middleware.

What still applies: **two-model-id resolution** (alias map redirects),
**audit row per call** (`protocol.anthropic_messages.invoked`,
`protocol.openai_responses.invoked`, `protocol.count_tokens.invoked`,
`protocol.translation_failed`), **rate limit** (`anthropic_messages
60/60s`, `anthropic_count_tokens 120/60s`, `openai_responses 60/60s`).

### Tool-use translation matrix

| Anthropic content block                                          | OpenAI Responses item                                              | Internal part            |
|------------------------------------------------------------------|--------------------------------------------------------------------|--------------------------|
| `{"type":"text","text":"…"}`                                     | `{"type":"input_text"|"output_text","text":"…"}`                   | `{"type":"text",...}`    |
| `{"type":"image","source":{"type":"base64",…}}`                  | `{"type":"input_image","image_url":"data:…"}` or `image_data`      | `{"type":"image_data"}`  |
| `{"type":"image","source":{"type":"url",…}}`                     | `{"type":"input_image","image_url":"https://…"}`                   | `{"type":"image_url"}`   |
| `{"type":"document","source":{"type":"base64",…}}`               | `{"type":"input_file","file_data":"data:…"}`                       | `{"type":"file_data"}`   |
| `{"type":"document","source":{"type":"url",…}}`                  | `{"type":"input_file","file_url":"…"}`                             | `{"type":"file_url"}`    |
| `{"type":"tool_use","id","name","input"}`                        | `{"type":"function_call","call_id","name","arguments"}`            | `{"type":"tool_use"}`    |
| `{"type":"tool_result","tool_use_id","content":[…]}`             | `{"type":"function_call_output","call_id","output"}`               | `{"type":"tool_result"}` |

Both sides round-trip through the same internal `tool_use` shape.

### What the gateway intentionally does NOT do

* No conversation chaining (`previous_response_id` / Anthropic
  persistence). Each call is one-shot.
* No real `file_search` / `web_search` execution — accepted at schema
  level so existing clients don't break, but the gateway never calls
  them.
* No SenHarness skills / memory injection (by design).
* No streaming on `/chat/completions` — use Messages or Responses.

---

## Provider routing

### Two-model-ID pattern (M2.5.7)

Decouple the **client-facing** model name from the **upstream** routing
target so swapping providers doesn't break provider-side prompt cache
prefixes.

* **`served_model_name`** — what `/v1/models` lists, what
  `tool_call_json.model_name` records, what audit metadata surfaces.
  Stable across upstream swaps.
* **`upstream_model_id`** — actual `provider:model` passed to the LLM.
  Changes when operators rotate providers.

### Data layer

`agents.served_model_name` — optional `String(120)`. NULL = upstream
name flows through unchanged. When set, runner advertises this name to
clients and records on every audit row.

`workspace.home_config_json["providers"]["served_alias_map"]`:

```json
{"providers": {"served_alias_map": {
  "ws-fast":     "deepseek:deepseek-chat",
  "ws-thinking": "openai:gpt-5"
}}}
```

Keys (`served_name`) and values (`upstream`) accept letters, digits,
`.`, `/`, `:`, `_`, `-`. Whitespace rejected at schema layer.

Resolution
([`resolve_served_model`](../backend/app/services/served_model.py)):

1. `agent.served_model_name` non-empty → use as `served_name`.
2. `fallback_upstream` (resolver's chosen upstream or per-turn
   `model_override`) → use as `served_name`.
3. Empty → `('', '', 'fallback')`; runner substitutes upstream as both.

Then alias map lookup: key match → `upstream` is mapped value,
`matched_via='workspace_alias'`. Else → `upstream == served_name`,
`matched_via` is `'agent_field'` or `'fallback'`.

Per-turn `model_override` always wins over alias map.

Audit: `provider.upstream_called` written **only** when alias map
redirected (`matched_via='workspace_alias'`). All other audit rows
(`tool_call`, `judge.*`, `evolver.*`, `cost.*`) record served name on
`metadata.model_name`.

Routes:

| Method | Path                                                              | Auth   | Purpose                                  |
|--------|-------------------------------------------------------------------|--------|------------------------------------------|
| GET    | `/api/v1/openai/v1/models`                                        | member | OpenAI-compat listing — served names only |
| GET    | `/api/v1/workspaces/{id}/settings/served-aliases`                 | member | Read alias map                            |
| PUT    | `/api/v1/workspaces/{id}/settings/served-aliases/{served}`        | admin  | Upsert one alias                          |
| DELETE | `/api/v1/workspaces/{id}/settings/served-aliases/{served}`        | admin  | Remove one alias                          |

### Failover chain (M2.5.3)

Outer recovery loop that switches across providers when one is durably
degraded. `pydantic-ai`'s built-in retry handles intra-provider transient
failures; the chain wrapper composes on top:

```
agent.iter()
  └── pydantic-ai per-provider retry (httpx + SDK back-off)
        └── on exhaustion: ProviderFailoverHint → chain wrapper picks next entry
```

Workspace config (`home_config_json["providers"]`):

| Key                          | Type        | Default | Meaning                                                                     |
|------------------------------|-------------|---------|-----------------------------------------------------------------------------|
| `failover_enabled`           | bool        | `false` | Master switch. Off = byte-for-byte identical to pre-M2.5.3 runner.          |
| `failover_chain`             | `list[str]` | `[]`    | Ordered `"provider:model"` candidates. Empty list inherits platform default. |
| `failover_max_attempts`      | int         | `3`     | Hard cap on chain entries tried per turn.                                   |
| `cooldown_threshold`         | int         | `3`     | Consecutive failures before entry is parked.                                |
| `cooldown_seconds`           | int         | `300`   | How long a tripped entry stays out of rotation.                             |

Platform default (`provider_failover` section): `enabled_default=false`,
`cooldown_threshold_default=3`, `cooldown_seconds_default=300`,
`failover_max_attempts_default=3`, `chain_global_default=[]`.

Chain build (`app.services.provider_chain.get_provider_chain`):

1. Parse `failover_chain` (or inherit platform default).
2. Synthesise single-element fallback from `primary_upstream` if parsed
   chain is empty — `failover_enabled=True` with no chain still routes
   primary through the wrapper for audit consistency.
3. Drop entries in cooldown (in-process cache + Redis ZSET tracker).
   When **every** entry is in cooldown → return original parsed chain.
   Failing closed here would silently drop the turn even though
   cooldown could be stale across worker processes.

Provider health tracker (`app.services.provider_health`): one snapshot
per `(provider_kind, model_id)`:

| Field                  | Meaning                                                              |
|------------------------|----------------------------------------------------------------------|
| `consecutive_failures` | Bumped on retryable failure, cleared on success.                     |
| `cooldown_until`       | UTC timestamp; `is_in_cooldown` returns True while `now < cooldown_until`. |
| `last_failure_at` / `last_success_at` | Diagnostic timestamps.                                |
| `last_failure_kind`    | `rate_limit` / `timeout` / `connection` / `5xx` / `auth` / `other`. |

Two-tier storage: in-process LRU cache (256 entries, hot-path reads) +
Redis hash (`provider_health:<kind>:<model>`). Both writes happen in
the same call; Redis errors fail open (treat provider as healthy).

Failure classification (`classify_exception` → six `FailureKind`
constants):

| Kind          | Retryable | Typical sources                                                 |
|---------------|-----------|-----------------------------------------------------------------|
| `rate_limit`  | yes       | `429`, `RateLimitError`, "too many requests"                    |
| `timeout`     | yes       | `TimeoutException`, "timed out", "deadline exceeded"            |
| `connection`  | yes       | `ConnectError`, `httpx.NetworkError`, SSL / DNS errors          |
| `5xx`         | yes       | HTTP 5xx, "service unavailable", "gateway timeout"              |
| `auth`        | no        | `401`, `403`, "unauthorized", "invalid api key"                 |
| `other`       | no        | Unrecognised — next provider almost certainly has same problem  |

**Message-history invariant (principle 5).** Chain wrapper passes the
**same** `RunRequest` (including `message_history`, `user_text`,
`policy`, `attachments`, `toolbox`) to every attempt. Locked by
`test_message_history_invariant.py` — deep-copies request payload per
attempt and asserts byte-equality. A reformatted history would shift the
upstream prompt cache prefix and silently regress cache hit rate,
defeating the point of failover.

**Stop on visible frame.** Chain wrapper deliberately stops trying after
the first attempt that produced a visible frame (DELTA / TOOL_CALL /
TOOL_RESULT / FINAL). Replaying after tokens streamed would either
confuse the user or modify the visible turn.

Audit: `provider.failover_attempted`, `provider.failover_succeeded`
(suppressed when entry 0 succeeded — `provider.upstream_called` is
enough), `provider.cooldown_started`, `provider.failover_exhausted`
(surfaced as `provider.chain_exhausted` ERROR + FINAL pair).

Notification `provider.cooldown_admin_alert`: `workspace_admins`, IN_APP,
1 h cooldown per `provider:model` resource id. `requires_email=False`.

---

## Approval dispatch (M2.5)

Bridge between an admin clicking *approve* on an Approval row and the
underlying state change actually happening — activating a SkillPackVersion,
archiving a SkillPack, creating a Flow, etc.

Components:

| Layer      | File                                                                                        | Responsibility                                  |
|------------|---------------------------------------------------------------------------------------------|-------------------------------------------------|
| Service    | [`app/services/approval_dispatch.py`](../backend/app/services/approval_dispatch.py)         | Per-`resource_type` apply handler.              |
| Service    | [`app/services/approval.py`](../backend/app/services/approval.py)                           | `approve_approval()` / `reject_approval()`.     |
| API        | [`app/api/v1/approvals.py`](../backend/app/api/v1/approvals.py)                             | `POST /approvals/{id}/decision` → dispatch.     |
| ARQ        | [`app/jobs/approval_ttl.py`](../backend/app/jobs/approval_ttl.py)                           | Hourly TTL processor (reminder + expiry).       |
| Migration  | `alembic/versions/0048_approval_reminder_sent.py`                                           | `reminder_sent` column + `(status, expires_at)` index. |

### Resource type → action

| `resource_type`               | Apply handler                                                                                          | Audit                                          |
|-------------------------------|--------------------------------------------------------------------------------------------------------|------------------------------------------------|
| `skill_pack_create`           | `activate_version(version_id)` + transition pack DRAFT→CANDIDATE→ACTIVE + `enabled=True`                | `evolver.applied_skill_pack_create`            |
| `skill_pack_patch`            | `activate_version(version_id)` (pack already ACTIVE)                                                   | `evolver.applied_skill_pack_patch`             |
| `skill_pack_edit`             | `activate_version(version_id)` (pack already ACTIVE)                                                   | `evolver.applied_skill_pack_edit`              |
| `skill_pack_delete`           | `transition(target=ARCHIVED, actor_kind=evolver, bypass_pinned=False)`                                 | `evolver.applied_skill_pack_delete`            |
| `skill_pack_archive` (curator)| same as delete but `actor_kind=curator`                                                                | `curator.applied_archive`                      |
| `skill_pack_write_file`       | create / update `SkillFile` row at `relative_path`                                                     | `evolver.applied_skill_pack_write_file`        |
| `skill_pack_remove_file`      | soft-delete `SkillFile` row (sets `deleted_at`)                                                        | `evolver.applied_skill_pack_remove_file`       |
| `flow_create`                 | create `Flow(enabled=False, …)` from cronjob propose body                                              | `evolver.applied_flow_create`                  |
| `hub_promotion`               | apply hub promotion (M3 hub workflow)                                                                  | `hub.promotion_applied`                        |
| `subagent_hallucination_review` | M2.5 Tenacity Pack flips spine row to COMPLETED                                                    | `subagent.hallucination_approved`              |
| `None` (legacy tool-call)     | no-op — returns `None`; legacy tool-call approval path keeps working unchanged                         | —                                              |

### Atomicity contract

`POST /approvals/{id}/decision`:

1. Load row + RBAC check (`require_decide_approval`).
2. **For approve**: call `dispatch_approved_approval(...)` on active session.
3. Flip `status` → `APPROVED` via repository.
4. Write `approval.decide` audit + cross-identity notification.
5. `await db.commit()`.
6. Wake the parked tool-call future (no-op for non-tool approvals).

If step 2 raises `DispatchError`: whole transaction rolled back (row
stays pending), API layer re-records `approval.dispatch_failed` audit on
fresh session, response is `409` with `{code, message, extras}`.

`PackPinnedAutoSkipped` is the one non-error short-circuit — when the
pack got pinned between propose and apply, dispatch returns
`DispatchResult(audit_action='approval.dispatch_skipped_pinned',
applied_object_id=None)` and the row still flips to APPROVED.

**Bulk decision.** `POST /approvals/bulk-decision` wraps each row in
`db.begin_nested()` so a dispatch failure on one row marks just that row
failed (with `error_code='approval.dispatch_*'`) and the rest of the
batch continues.

**Breaker reset on success.** Dispatch handler succeeds + `resource_type`
is an M2.x evolver-sourced verb → reset workspace's
`evolver:fail:<workspace_id>` Redis breaker. Curator
(`skill_pack_archive`) does not reset — curator pipeline owns its own
audit trail.

### TTL processor

`process_expired_approvals` runs hourly (cron minute 22).

**Pass 1 — pre-expiry reminder.** For pending approvals with
`expires_at <= now + 24h` AND `reminder_sent = False`:

* Emit `approval.expiring` notification via M0.10 (in-app + email; admin
  recipients per workspace audience).
* Flip `reminder_sent` → `True`.
* Write `approval.expiring_reminder_sent` audit.

**Pass 2 — expired processor.** For pending approvals with `expires_at
<= now`:

| `resource_type`                                  | TTL action                                                       | Audit                            |
|--------------------------------------------------|------------------------------------------------------------------|----------------------------------|
| `skill_pack_archive`                             | **auto-execute** (spirit of "if nobody objects, archive it")     | `approval.expired_auto_executed` |
| `skill_pack_create` / `_patch` / `_edit`         | REJECT (status → `EXPIRED`)                                      | `approval.expired_rejected`      |
| `skill_pack_delete` / `_remove_file` / `_write_file` | REJECT                                                       | `approval.expired_rejected`      |
| `flow_create`                                    | REJECT                                                           | `approval.expired_rejected`      |
| `hub_promotion`                                  | REJECT                                                           | `approval.expired_rejected`      |
| `subagent_hallucination_review`                  | REJECT                                                           | `approval.expired_rejected`      |
| `None` (legacy tool-call)                        | REJECT (defensive — in-runtime callback already 5-min times out) | `approval.expired_rejected`      |

ARQ permanent-failure (3 strikes) writes `approval.ttl_failed_permanent`
+ shared `job.failed_permanent` notification.

UI: [`ApprovalCard.tsx`](../frontend/src/components/approvals/ApprovalCard.tsx)
picks one of seven inner renderers based on `resource_type` (skill
version with embedded SkillDiffPanel, archive/delete metadata, file
write/remove with relative path + excerpt, flow create with schedule +
prompt template + delivery channels, legacy tool-call). List page
`/approvals` filters by `resource_type`, sorts by `expires_at ASC`,
shows red countdown chip < 60s / amber < 120s.

---

## Notifications

### Event registry (M0.10)

`backend/app/services/notification_events.py`. `emit_event(...)` is the
single public entry point.

| Key                                  | Audience           | Default channels    | Cooldown | `requires_email` |
|--------------------------------------|--------------------|---------------------|----------|------------------|
| `goal.alignment_low`                 | owner              | in_app              | 600 s    | no               |
| `goal.locked` / `goal.unlocked`      | owner              | in_app              | 0        | no               |
| `judge.score_negative`               | owner              | in_app              | 600 s    | no               |
| `judge.degraded`                     | workspace_admins   | in_app              | 3600 s   | no               |
| `channel.sender_blocked`             | workspace_admins   | in_app + email      | 600 s    | yes              |
| `security.signature_failed`          | workspace_admins   | in_app + email      | 0        | yes              |
| `auth.workspace_provisioned`         | actor              | in_app + email      | 0        | yes              |
| `workspace.quota_exceeded`           | actor              | in_app              | 600 s    | no               |
| `workspace.spike_detected`           | platform_admins    | in_app              | 1800 s   | no               |
| `workspace.quota_increased`          | actor              | in_app + email      | 0        | no               |
| `job.failed_permanent`               | workspace_admins (fallback platform_admins) | in_app + email | 300 s | yes |
| `approval.expiring`                  | workspace_admins   | in_app + email      | 3600 s   | yes              |
| `platform_settings.changed`          | platform_admins    | in_app              | varies   | varies           |
| `subagent.zombie_detected`           | workspace_admins   | in_app              | 5 min    | no               |
| `provider.cooldown_admin_alert`      | workspace_admins   | in_app              | 1 h      | no               |
| `inflight_run.lost_detected`         | actor              | in_app              | 0        | no               |
| `inflight_run.force_recycled`        | actor              | in_app              | 0        | no               |
| `cache.adaptive_disabled`            | workspace_admins   | in_app              | 1 h      | no               |

Audience semantics:

| Audience          | Resolves to                                                                   |
|-------------------|-------------------------------------------------------------------------------|
| `actor`           | `actor_identity_id` argument (no DB hit).                                     |
| `owner`           | Every `OWNER` membership in the workspace.                                    |
| `workspace_admins`| Every `OWNER` or `ADMIN` membership in the workspace.                         |
| `platform_admins` | Every active identity with `platform_role = platform_admin`.                  |
| `broadcast`       | Every active membership in the workspace.                                     |

Callers can override resolver by passing `target_identity_ids=[…]`
directly — auth / quota-override paths do this when recipient is known.

### Cooldown / dedup

```
notif_dedup:<event_key>:<workspace_id_or_global>:<identity_id>:<cooldown_resource_id>
```

Redis `SET NX EX <cooldown_seconds>`. Repeated emit inside window →
`cooldown_skipped` counter. `cooldown_seconds=0` short-circuits Redis
call entirely. Redis unreachable → fails open (notification is sent —
dedup is optimisation, not correctness gate).

### Per-identity preferences

Stored in `identities.notification_prefs_json` (JSONB, alembic 0041):

```json
{
  "<event_key>": {"channels": ["in_app", "email"], "muted": false},
  "_global": {"muted_until": "2026-12-31T00:00:00Z"}
}
```

Merge order in `_effective_channels`:

1. Per-event `muted=True` → empty channel set, **except** when
   `requires_email=True` — the email floor cannot be opted out by the
   user. Only a platform admin (system setting toggle) can suppress
   security mail.
2. Per-event `channels=[…]` intersects descriptor defaults + requires_email floor.
3. `_global.muted_until` against `datetime.now(UTC)`; future timestamp
   drops recipient entirely.
4. Platform `notification_defaults.platform_email_critical_only`
   (default `True`) drops EMAIL from non-`requires_email` events.

### Channels + transport

Today: `IN_APP` (inserts `Notification` row + pushes through per-
(workspace, identity) websocket hub) + `EMAIL` (enqueues
`send_email_notification` ARQ task). IM channels reserved for M2.5+.

`app.services.email_transport.get_email_transport()` returns
process-wide singleton. Default `LogEmailTransport`: writes one
`email.dispatched_via_log` audit keyed on `sha256(to_email)[:16]` (never
leaks raw addresses), emits one INFO log with same hash + subject,
always returns `EmailDispatchResult(ok=True)`.

Plug a real adapter (SMTP, Mailgun, etc.) by implementing
`EmailTransport.send`, reading
`system_settings.notification_defaults.email_transport_kind`, and
calling `set_email_transport(adapter)` from app startup.

Job-level retry contract: 3 strikes → `job.failed_permanent` audit
through ARQ `on_job_end` hook in `app.worker.arq_app`. Independent of
chosen adapter.

### Cleanup cron

`cleanup_old_notifications` daily at 03:30 UTC. Hard-deletes
`Notification` rows older than
`notification_defaults.in_app_retention_days` (default 90 days). Writes
one `notification.cleanup_swept` audit per run. Intentionally separate
from M0.11 retention sweep — notifications are workspace-scoped derived
data, not user-original content.

Audit: `notification.emitted`, `notification.cooldown_skipped`,
`notification.emit_failed`, `notification.preferences_updated`,
`notification.cleanup_swept`, `email.dispatched_via_log`,
`email.dispatched_via_smtp` (reserved).

### Notification UX (M4.7)

Three surfaces:

| Surface              | Where                              | Purpose                                              |
|----------------------|------------------------------------|------------------------------------------------------|
| Sidebar bell         | Bottom-left of authenticated pages | Live unread badge + popover (10 most recent rows)    |
| Inbox page           | `/notifications`                   | Full filterable list, drawer with payload + jump-to-source |
| Preferences page     | `/settings/notifications`          | Per-event channel toggles + quiet hours              |

Bell shows red badge with unread count (`99+` after 99). Popover footer
has "View all notifications" → inbox + "Notification settings" →
preferences. WebSocket at `/api/v1/notifications/ws` pushes new rows
live; bell also polls `unread-count` every 30 s as safety net.

Inbox auto-refreshes every 30 s. Toolbar: status filter (All / Unread /
Read), urgency filter, event filter, search (300 ms debounce against
title/body/event_key), `Mark all read`, `Refresh`. Default page size
50; "Load more" appends next 50 (max 200).

Detail drawer sections: title + urgency badge + unread badge, body
verbatim, meta (event key / urgency / received-at / read-at / actor id),
payload as flat `key → value` table (nested objects JSON-stringified),
"Open in source" button (or muted hint when no deep link), mark-as-
read/unread toggle.

**Open-in-source map** (conservative — when the dispatcher's payload
doesn't carry deep-link field, resolver sends user to parent admin page
rather than fabricating URL):

| Event family                                                       | Destination                                       |
|--------------------------------------------------------------------|---------------------------------------------------|
| `goal.alignment_low` / `goal.locked` / `goal.unlocked`             | `/chat/{session_id}` (session goal banner)        |
| `judge.score_negative`                                             | `/traces/{session_id}` (trace tab, run row)       |
| `judge.degraded` / `provider.cooldown_admin_alert` / `cache.adaptive_disabled` | `/settings/workspace/providers`          |
| `channel.sender_blocked`                                           | `/channels?channel={channel_id}`                  |
| `security.signature_failed`                                        | `/settings/audit`                                 |
| `workspace.quota_exceeded` / `workspace.spike_detected` / `workspace.quota_increased` | `/settings/workspace/quota`        |
| `auth.workspace_provisioned`                                       | `/settings/profile`                               |
| `job.failed_permanent`                                             | `/settings/system/jobs`                           |
| `approval.expiring`                                                | `/approvals`                                      |
| `subagent.zombie_detected` / `inflight_run.force_recycled`         | `/settings/system/runtime`                        |
| `inflight_run.lost_detected`                                       | `/chat/{session_id}` if known, else runtime console |
| `platform_settings.changed`                                        | `/admin`                                          |

If dispatcher set `notification.action_url`, that wins — backend has
freshest info (e.g. can pin to specific `message_id`, not just session).

**Preferences page** stacks two cards:

1. **Quiet hours** — single global mute window applies to **every**
   event including `requires_email=True` security alerts. Picker is
   `<input type="datetime-local">`; local time converted to UTC before
   PUT.
2. **Event-by-event** — one row per visible event. Mute switch suppresses
   unless `requires_email=True` (Email chip permanently locked with
   tooltip).

### Adding a new event

1. Add `EventDescriptor` to `EVENT_REGISTRY` including `title_key` +
   `message_key` under `notification.<key>.title|message` in both locale
   JSON files.
2. Call `emit_event(...)` immediately after `audit_events` write. Wrap
   in try/except so notification cannot break original transaction; the
   fan-out audits its own crashes via `notification.emit_failed`.
3. Pick a sensible `cooldown_resource_id`: usually the entity the user
   thinks of as "the same alert" (channel, session goal, workspace,
   identity).
4. If operationally critical (security / welcome / permanent failure),
   set `requires_email=True`.

---

## Evaluation (L5)

Independent evaluation path, external to the agent's main loop. Source:
[`backend/app/agents/harness/evaluator.py`](../backend/app/agents/harness/evaluator.py).

**Core observation:** an LLM is a terrible QA engineer for its own
output. Asking the same model whether its answer is correct returns an
optimistic grade ~90% of the time.

Four pieces:

1. **Heuristic scoring** (always on, zero deps). Docks score when it
   sees empty answer, answer much shorter than question, hallucination
   markers (`as an AI language model`, `I cannot verify`), refusal
   patterns, or tool-call narration without actual result. Mapping:
   `score >= 0.8 → pass`, `≥ 0.5 → warn`, else `fail`.
2. **Auxiliary-LLM scoring** (optional). Workspace `eval_model` →
   independent pydantic-ai agent with strict JSON verdict over
   relevance / faithfulness / safety. Blended with heuristic:
   `0.7·aux + 0.3·heuristic`. Unparseable → heuristic stands.
3. **NLI consistency** (optional, requires `semantix-ai`). Mean
   entailment probability between final answer (hypothesis) and every
   tool/knowledge snippet consumed (premises). Low NLI score (< 0.5)
   deducts extra 0.15.
4. **Trace replay + Prometheus** (always on).

```
Agent run ends ──► evaluate_run() ──► write eval dict into message.metadata
   (FINAL event)   heuristic + aux + NLI         │
                                                 ▼
                                        record_eval(verdict) → Prometheus
```

Evaluation is **fire-and-forget**: final response reaches user first,
then evaluator runs. A failed evaluator never kills the run.

Operator surface:

* **Trace replay** `/traces/{session_id}` — verdict badge next to each
  assistant message: `[pass · 0.92 · NLI 0.87]`. Full reasons array in
  expanded panel.
* **Prometheus** `senharness_eval_verdict_total{verdict="pass|warn|fail"}`
  updated on every evaluation. Surfaced on admin Observability page.

Other counters: `senharness_agent_runs_total{provider, model, status}`,
`senharness_agent_run_duration_seconds`,
`senharness_agent_tokens_total{kind}`, `senharness_agent_cost_usd_total`,
`senharness_tool_calls_total{tool, status}`, HTTP request counters +
duration histogram.

**Request-to-trace correlation.** Every inbound HTTP request gets
`X-Request-ID`. `RequestIDMiddleware` attaches it as
`senharness.request_id` on active OpenTelemetry span — downstream
Logfire / Jaeger / Langfuse exporter produces spans filterable by
request id. Same id comes back on response header.

Workspace config (`metadata.eval`):

```json
{"eval": {
  "enabled": true,
  "aux_model": "openai:gpt-4o-mini",
  "max_sample_rate": 0.1
}}
```

Agent-level override on `Agent.metadata.eval`. Use to enforce 100%
sampling on production-critical agents and 1% on volume-heavy chat
assistants.

---

## Profiles

### Agent profile (M3.4)

Per-agent cumulative strengths (toolset / skill-category / domain
effectiveness), failure modes (clustered from negative judges), and a
platform-admin-only cross-workspace rollup.

`agent_profiles` is workspace-scoped, soft-deleted, **strictly 1:1 with
`agents`** (`UNIQUE(agent_id)`).

```jsonc
// strengths_json
{
  "toolsets": [{"name": "browser", "use_count": 18, "effectiveness_avg": 0.72}],
  "skill_categories": [{"category": "data-analysis", "use_count": 9}],
  "domains": [{"domain": "marketing", "use_count": 6, "judge_avg": 0.5}],
  "sample_artifact_count": 14
}

// failure_modes_json
{
  "hallucination_kinds": [{"kind": "tool_arg_invented", "count": 5}],
  "common_errors": [{"error_kind": "rate_limit", "count": 8}],
  "error_patterns": [{"pattern_summary": "Forgets to back off when 429", "frequency": 4}]
}

// cross_workspace_stats_json (platform admin only)
{
  "total_runs_across_tenants": 142,
  "median_judge_score": 0.0,
  "top_failure_kinds": [{"error_kind": "auth", "count": 11}],
  "workspace_count": 4,
  "judged_run_count": 89
}
```

Successful artifacts (`final_outcome="success"` AND judge ≥ 0) drive
strengths. Aux LLM (task `SKILL_REVIEW`) clusters recent score=-1
artifacts' `JudgeVerdict.process_notes_json` into failure-modes buckets.
Aux fallback to heuristic baseline (top `error_kind` only) when: evolver
breaker open, no aux model, aux call timed out/raised/unparseable. In
every fallback case row is still updated + `agent_profile.aux_skipped`
audit.

`cross_workspace_stats_json` recomputed on every
`/admin/agents/{id}/profile/cross-workspace` read. **Never returned** by
workspace-scoped read; `AgentProfileRead` vs `AgentProfileAdminRead`
schemas enforce at response-model level; service raises
`CrossWorkspaceStatsForbidden` if non-platform-admin reaches via any
other path.

Routes:

| Method | Route                                                  | RBAC             | Rate                              |
|--------|--------------------------------------------------------|------------------|-----------------------------------|
| GET    | `/agents/{agent_id}/profile`                           | workspace member | `agent_profile_read 60/60s`       |
| POST   | `/agents/{agent_id}/profile/refresh`                   | workspace admin  | `agent_profile_refresh 5/300s`    |
| GET    | `/admin/agents/{agent_id}/profile/cross-workspace`     | platform admin   | `agent_profile_admin_read 30/60s` |

Cron `update_agent_profiles_sweep` daily at **05:00 UTC**.

Audit: `agent_profile.updated`, `agent_profile.refresh_triggered`,
`agent_profile.aux_skipped`, `agent_profile.cross_workspace_stats_accessed`,
`agent_profile.update_failed`, `agent_profile.sweep_failed_permanent`.

### User profile (M3.7)

Per-identity, 12-dimension fact set populated daily from recent
`session_artifacts`. Runtime folds into every system prompt under the
M0.7 always-on hard cap. User can directly `Confirm` or `Reject` through
`/me/profile`.

**Dialectic.** Each dimension is a *conversation* between three parties:

1. Aux LLM (proposes fact + confidence).
2. Auto-injection rule (`confidence ≥ 0.7` → injected without
   confirmation; otherwise pending).
3. User (Confirm / Reject).

`Confirm` permanently elevates a low-confidence row to "always inject";
`Reject` permanently demotes any row to "never inject" (`user_rejected=True`
→ permanently never injected). Successive extractions add candidate
rows and back-fill the parent's `superseded_by_id`.

The 12 dimensions (`UserProfileDimension` enum in
`backend/app/db/models/user_profile.py`):

| Key                     | Purpose                                             |
|-------------------------|-----------------------------------------------------|
| `communication_style`   | Concise / verbose / structured preference           |
| `domain_expertise`      | Demonstrated working knowledge across domains       |
| `decision_preference`   | Trade-offs spelled out vs. single recommendation    |
| `tone_preference`       | Friendly / neutral / technical register             |
| `language_primary`      | Default human language                              |
| `working_hours`         | Active windows (used to throttle proactive outreach)|
| `autonomy_tolerance`    | Acceptance of un-asked agent initiative             |
| `detail_preference`     | Bullet summary vs. step-by-step rationale           |
| `formality`             | Casual vs. formal honorifics                        |
| `proactivity_tolerance` | Tolerance for unsolicited suggestions / follow-ups  |
| `domain_interest`       | Topics the user keeps returning to                  |
| `goal_pattern`          | Recurring goal shapes (deadlines, milestones)       |

Adding a new dimension: add the StrEnum value + i18n key under
`messages/<locale>.json/userProfile.dimensions`. No migration —
`Enum(..., native_enum=False)`.

**Injection contract** (`render_facts_for_injection`):

| Row state                                         | Injected? |
|---------------------------------------------------|-----------|
| `user_rejected=True` (any confidence)             | Never     |
| `user_confirmed=True` (any confidence)            | Always    |
| `user_confirmed=False` AND `confidence ≥ 0.7`      | Auto      |
| `user_confirmed=False` AND `confidence < 0.7`      | Pending   |

Two non-rejected rows tie for same dimension → prefer `user_confirmed=True`
over higher-confidence-but-unconfirmed (explicit user trust beats
freshness).

Composed block respects M0.7 hard cap (4000 chars total default).
Per-line cap `PER_LINE_MAX_CHARS=300` prevents one runaway dimension
from starving others. Total cap fires → renderer drops bullets on line
boundary, appends `[truncated]`.

Routes (`/api/v1/me/profile/*`, scoped to caller's active workspace):

| Method | Route                                | RBAC          | Rate                              |
|--------|--------------------------------------|---------------|-----------------------------------|
| GET    | `/me/profile`                        | identity      | `me_profile_read 60/60s`          |
| POST   | `/me/profile/{fact_id}/confirm`      | identity owner| `me_profile_action 30/60s`        |
| POST   | `/me/profile/{fact_id}/reject`       | identity owner| `me_profile_action 30/60s`        |
| POST   | `/me/profile/extract-now`            | identity      | `me_profile_extract 3/300s`       |

Cross-identity isolation: workspace_id filter + repo identity check
both fail to match foreign-identity row → 404 (never 403, so existence
stays opaque).

Cron `extract_user_facts_sweep` daily at **05:30 UTC** (30 min after
agent-profile sweep). Walks every identity with ≥ 1 artifact in last 30
days. Per-identity exceptions isolated under
`user_profile.update_failed`.

`identity_id` is **never** embedded in audit metadata as raw UUID —
every row uses `uuid5(NAMESPACE_OID, identity_id).hex[:16]`.

Audit: `user_profile.facts_extracted`, `user_profile.fact_confirmed`,
`user_profile.fact_rejected`, `user_profile.fact_superseded`,
`user_profile.injection_rendered`.

Same identity in two workspaces → entirely independent models because
the same person can behave differently in *Acme Corp* vs. *Personal*.

---

## Platform settings (M0.13)

`/admin/settings/<section>` is the single console for every
platform-wide configuration. 14 schema-driven sections share one
persistence model (`system_settings` JSONB rows), one cross-process
invalidation channel, one audit/notification pipeline.

| Section                | Schema                                            | Email-notify | Dangerous fields                                |
|------------------------|---------------------------------------------------|--------------|-------------------------------------------------|
| `general`              | `GeneralSettings`                                 | –            | –                                               |
| `auth.registration`    | `AuthRegistrationSettings`                        | yes          | `mode → closed`                                 |
| `auth.oauth`           | `AuthOAuthSettings`                               | yes          | –                                               |
| `auth.mfa`             | `AuthMfaSettings`                                 | –            | –                                               |
| `email.smtp`           | `EmailSmtpSettings`                               | –            | –                                               |
| `workspace.quota`      | `WorkspaceQuotaSettings` (M0.12)                  | –            | –                                               |
| `workspace.defaults`   | `WorkspaceDefaultsSettings`                       | –            | –                                               |
| `security.shields`     | `SecurityShieldsSettings`                         | yes          | –                                               |
| `security.sandbox`     | `SecuritySandboxSettings`                         | yes          | `allow_local_execute_in_prod`, `allow_ssh_backend` |
| `evolver`              | `EvolverSettings`                                 | –            | –                                               |
| `notifications`        | `NotificationDefaults` (M0.10)                    | –            | –                                               |
| `plugins`              | `PluginsSettings`                                 | yes          | `allow_user_plugins`, `allow_unapproved_plugins` |
| `retention`            | `RetentionSettings` (M0.11)                       | –            | –                                               |
| `memory`               | `MemoryDefaults` (M0.7)                           | –            | –                                               |

### Storage + precedence

Reads resolve:

```
in-process cache (TTL = 30s)
  → DB row in system_settings
  → env-bootstrap value (SMTP_*, OAUTH_<NAME>_*, AUTH_REGISTER_RATE_LIMIT, …)
  → Pydantic schema default
```

DB always wins once admin saves through UI. `.env` values only matter
for first boot; after bootstrap seeds them into `system_settings`, env
vars become advisory — admin UI shows small `.env` badge next to each
field whose current value still matches an env var.

### Cross-process invalidation

`update_section` / `reset_section` publish section name on Redis channel
`platform_settings:invalidated`. Every backend process subscribes during
startup (`start_invalidation_listener`) and drops its local cache entry
on receipt. Convergence: Redis up ~1 s; Redis down ≤ 30 s (cache TTL).
Publish failure logs INFO, never raises.

### Bootstrap from `.env`

`bootstrap_from_env_if_empty(db)` runs on lifespan startup and once per
fresh deploy. Only writes a section when **no** corresponding row exists
in `system_settings`. Once admin UI saves a value, env-bootstrap is a
no-op for that section. Re-running on a seeded DB is safe.

Mapping in `app.services.platform_settings.ENV_FIELD_MAPPING` plus
`_build_smtp_payload_from_env`, `_build_oauth_payload_from_env` for
composite seeding.

### Dangerous-change confirmation

`PUT /admin/platform-settings/{section}` returns
`400 platform_settings.dangerous_change_requires_confirmation` when a
flagged field changes and `confirmed_dangerous=False` in body. Frontend
renders extra modal listing flagged fields and re-issues PUT with
`confirmed_dangerous=True`.

Every dangerous change writes two audit rows:

* `platform_settings.updated` with redacted diff (secret fields
  `password`, `password_ref`, `client_secret`, `client_secret_ref`,
  `signing_root_pubkey` replaced with `"***"`).
* `platform_settings.dangerous_change` listing flagged fields.

### Email transport reload

`email.smtp` is the only section whose change triggers a side-effect
beyond cache invalidation: post-update hook calls
`reload_email_transport_from_settings(db)` swapping the process-wide
`_DEFAULT_TRANSPORT` singleton. Other workers receive Redis invalidation
+ call the same reload helper from the listener. When `enabled=False`,
`host` unset, or `from_address` unset, reload silently falls back to
`LogEmailTransport`.

### Test buttons

* `POST /admin/platform-settings/email.smtp/test` builds an in-memory
  `SmtpEmailTransport` from posted payload (NOT from DB row), sends one
  test to calling admin's verified email, audits
  `platform_settings.smtp_test`. Persistence still requires save.
* `POST /admin/platform-settings/auth.oauth/{provider}/test` calls
  provider's discovery / well-known endpoint and reports reachability.

Both rate-limited to 5 / 5 min per admin.

Audit: `platform_settings.updated`, `platform_settings.dangerous_change`,
`platform_settings.bootstrapped_from_env`, `platform_settings.smtp_test`,
`platform_settings.oauth_test`. Notification
`notification_events.platform_settings.changed` fans out to other
platform admins (in-app); for sections in `EMAIL_NOTIFY_SECTIONS` email
also enqueued **bypassing cooldown**, keyed by section + day.

---

## Registration & onboarding (M0.9)

Three registration modes via `registration_mode` system setting:

| Mode                | Self-register?            | Invitation?        | Personal workspace? | Auto-login tokens? |
|---------------------|---------------------------|--------------------|---------------------|---------------------|
| `open_personal` *(default)* | Yes               | Optional           | Yes                 | Yes (when email verification off) |
| `open_invite_only`  | Yes, only with `invitation_code` | Required    | No (joins inviter's) | No                  |
| `closed`            | No — 403 `auth.registration_closed` | n/a       | n/a                 | n/a                 |

Frontend reads `GET /api/v1/auth/registration-mode` (public, 60/min) on
register page.

### Personal workspace slug allocator

`app/services/personal_workspace.py` derives slug from email local-part:

1. Sanitize: lowercase, strip `+tag`, replace `.` → `-`, drop chars
   outside `[a-z0-9-]`, collapse `-` runs, trim trailing `-`, cap 60.
   Empty fallback `user`.
2. Reserved OR taken → append `-2 .. -9` and re-check.
3. Still unavailable → append 6-hex random tail (`-a3f9c1`). Up to 5
   attempts before falling back to `u-{token_hex(6)}`.

Only the random-tail path returns `workspace_slug_warning=true` —
linear `-N` is silent. Frontend stores warning in `sessionStorage` and
surfaces single toast on first arrival.

**Reserved slugs.** Static base set in `personal_workspace.py`:
`admin`, `platform`, `api`, `hub`, `system`, `public`, `settings`,
`login`, `register`, `oauth`, `auth`, `_health`, `metrics`, `internal`,
`static`, `assets`, `files`, `uploads`, `downloads`, `ws`, `websocket`,
`callback`, `logout`, `me`, `users`, `workspaces`, `agents`, `channels`,
`flows`, `skills`, `memory`, `memories`, `approvals`, `audit`,
`notifications`, `test`, `debug`, `root`, `owner`, `moderator`,
`support`, `help`, `docs`, `blog`, `status`, `billing`, `pricing`,
`terms`, `privacy`, `senharness-system`.

Platform-wide extras via `reserved_workspace_slugs_extra` system setting.
`get_reserved_slugs(db)` lower-cases and trims every entry, unions with
base set on every register call.

### Email verification gate

When `auth_require_email_verification=true`:

1. Register creates identity with `status=pending`; no auto-login tokens.
2. Auth service mints opaque `secrets.token_urlsafe(32)`, stores SHA-256
   digest in `email_verification_tokens`, queues delivery via
   `send_verification_email`. SMTP wired in M0.13; meanwhile token +
   recipient land in `auth.email_verification_sent` audit row + INFO log.
3. User submits token to `POST /api/v1/auth/verify-email/{token}`. Row
   marked consumed; identity flips to `active`.
4. Every API route except the whitelist below 403s a PENDING identity
   with `code=auth.email_unverified`. Enforced by
   `EmailVerificationGateMiddleware`.

PENDING-safe whitelist:

| Path                                     | Why                                                       |
|------------------------------------------|-----------------------------------------------------------|
| `/api/v1/me`                             | Verify-email-pending screen shows identity name           |
| `/api/v1/auth/logout`                    | Always idempotent; clears auth cookie                     |
| `/api/v1/auth/verify-email/*`            | The endpoint that flips status to active                  |
| `/api/v1/auth/resend-verification`       | Deliberate rate-limited resend                            |
| `/api/v1/auth/registration-mode`         | Read-only public meta endpoint                            |
| `/api/v1/auth/refresh`                   | Cookie-based; PENDING tokens still need refresh           |
| `/api/v1/auth/oauth/*`                   | OAuth callbacks must complete unhindered                  |
| `/api/v1/health`, `/api/v1/version`      | Liveness probes                                           |
| Any path outside `/api/`                 | `/admin/sql/`, `/docs`, etc.                              |

### Auto-login tokens

Register response carries `auto_login_tokens` exactly when **all** of:

* `registration_mode == "open_personal"`, AND
* `auth_require_email_verification == false`, AND
* a personal workspace was actually provisioned (no `invitation_code`).

Same TTLs as normal login. Refresh in `sh_refresh` HttpOnly cookie;
access in JSON body. Frontend stores access in `useAuthStore`, pushes
to `/?onboarding=1` re-arming `OnboardingTour`.

### Routes

| Method | Path                                  | Rate                         | Notes                                                |
|--------|---------------------------------------|------------------------------|------------------------------------------------------|
| POST   | `/api/v1/auth/register`               | `auth_register` (default 3/60s, env-tunable) | Returns `RegistrationResponse`         |
| GET    | `/api/v1/auth/registration-mode`      | `auth_meta_read 60/60s`      | Public                                               |
| POST   | `/api/v1/auth/verify-email/{token}`   | `auth_verify_email 10/60s`   | 204 on success                                       |
| POST   | `/api/v1/auth/resend-verification`    | `auth_resend_verify 3/300s`  | 204 even when email unknown (anti-enumeration)       |

Audit: `auth.registered`, `auth.workspace_provisioned`,
`auth.email_verification_sent`, `auth.email_verification_resent`,
`auth.email_verified`.

---

## Workspace creation quota (M0.12)

Per-identity guardrail against `POST /workspaces` abuse. Four levers:
source-kind defaults, per-identity override, sliding rate window, slug
tombstones.

Default budget:

| Source kind          | Default | Origin                                  |
|----------------------|---------|-----------------------------------------|
| `self_register`      | 1       | personal workspace at register only     |
| `oauth_register`     | 1       | personal workspace at OAuth onboarding  |
| `admin_provision`    | 20      | platform admins provisioning tenants    |
| `invitation_redeem`  | 0       | invitee joins existing workspace        |

`creation_allowed_for_self_registered` defaults **off** — self-registered
identity keeps personal workspace but can't click "+ New Workspace"
until platform admin flips toggle or sets override.

`infer_source_kind` decides which default applies:

1. `Identity.oauth_provider` set → `oauth_register`.
2. `Identity.platform_role == platform_admin` → `admin_provision`.
3. Earliest row in `workspace_creation_logs` for this identity wins.
4. Fall back to `self_register`.

`Identity.workspace_quota_override` always trumps default; clearing it
reverts to inferred default.

**Rate window.** Max `creation_rate_per_period` (default 2) attempts per
`creation_rate_period_seconds` (default 1 h). Counter increments on
every `check_can_create` call **including** failed ones — attackers
can't probe for free. Counter is larger of DB rows in
`workspace_creation_logs` + in-process ledger.

**Slug tombstones.** `DELETE /workspaces/{id}` soft-deletes + flips
`slug_tombstoned=TRUE`. Both personal-workspace allocator and
`POST /workspaces` consult `is_slug_tombstoned` and refuse reuse. Closes
the create→delete→re-create slug-squat attack.

Audit: `workspace.created`, `workspace.deleted`, `workspace.quota_freed`,
`workspace.quota_exceeded`, `workspace.creation_rate_limited`,
`workspace.creation_not_permitted`, `workspace.quota_override_set`.
Failed-attempt audits run on fresh DB session so row survives route
rollback.

Grandfather migration `0037_workspace_creation_quota` runs SQL backfill:

```sql
SELECT m.identity_id, COUNT(*) AS owned_count
FROM memberships m
JOIN workspaces w ON w.id = m.workspace_id
WHERE m.role = 'owner' AND m.deleted_at IS NULL
  AND m.status = 'active' AND w.deleted_at IS NULL
GROUP BY m.identity_id
HAVING COUNT(*) > <default_per_self_registered>
```

Sets `workspace_quota_override = owned_count` (only when still NULL) so
legacy power users keep their fleet.

Routes:

| Method | Route                                                | Auth            | Notes                                  |
|--------|------------------------------------------------------|-----------------|----------------------------------------|
| GET    | `/api/v1/me/workspace-quota`                         | active identity | Budget + rate-window snapshot          |
| POST   | `/api/v1/workspaces`                                 | active identity | Runs quota check + tombstone gate      |
| DELETE | `/api/v1/workspaces/{id}`                            | workspace owner | Tombstones slug + frees quota slot     |
| GET    | `/api/v1/admin/workspace-quotas`                     | platform admin  | Rows sorted by usage                   |
| GET    | `/api/v1/admin/workspace-quotas/{identity_id}`       | platform admin  | Single row                             |
| PATCH  | `/api/v1/admin/identities/{id}/workspace-quota`      | platform admin  | `{quota: int | null}`; null clears     |

---

## Retention cascade (M0.11)

Two-stage GDPR pipeline. When identity/workspace is soft-deleted,
**nothing happens synchronously**; a cron-driven sweep cascades the
deletion across every table holding user-derived data.

```
identity / workspace soft-delete
        │  (no event hook — sweep notices via watermark)
        ▼
retention_sweep_cascade   (every 5 min)
        │  cascade_for_identity / cascade_for_workspace
        ▼
target tables get deleted_at = now()   (or DELETE for short-lived rows)
        │  retention_days elapses (default 30)
        ▼
retention_physical_purge  (daily 04:00 UTC)
        │  audit "would purge" while physical_purge_enabled=False
        │  DELETE FROM … once operator flips flag
        ▼
rows are physically gone
```

**Why sweep, not hooks.** Idempotent, immune to missed call site (no
per-feature wiring to forget), observable from one cursor row
(`retention_watermarks`).

### `CASCADE_TARGETS`

Single source of truth in
[`app.services.retention.CASCADE_TARGETS`](../backend/app/services/retention.py).
Each entry is `CascadeTarget(table_name, soft_delete, workspace_scoped,
identity_scoped, retention_days_override_key)`.

Current targets include: `session_goals`, `goal_alignment_scores`,
`session_artifacts`, `judge_verdicts`, `pending_memories`,
`email_verification_tokens` (7 d), `workspace_creation_logs` (180 d),
`skill_usage`, `skill_lineage_edges`, `workspace_hub_subscriptions`,
`logical_threads`, `thread_channel_bindings`, `project_boards`,
`board_cards`, `user_profile_facts`, `agent_profiles`.

Adding a new target: append a `CascadeTarget(...)` row. No other code
change. If target table not yet migrated → sweep silently skips
(per-target `inspect(conn).has_table` check), so target can land
ahead of migration without breaking the worker.

**SQL safety.** Identifiers (table / column / parent FK column) come
exclusively from in-source whitelist; `_safe_ident` enforces strict
regex. All scope IDs are bound parameters, never string-interpolated.

### `RetentionSettings`

```python
class RetentionSettings(BaseModel):
    default_days: int = 30           # 1-3650
    per_table_days: dict[str, int] = {
        "email_verification_tokens": 7,
        "workspace_creation_logs": 180,
    }
    physical_purge_enabled: bool = False
    sweep_batch_size: int = 100      # 10-10_000
```

**`physical_purge_enabled` defaults False** so operator observes
dry-run candidate counts in audit feed for a week or two before
flipping.

### Audit trail + identity hashing

`data.cascade_soft_delete` per successful cascade; `data.physical_purge`
per purge run; `job.failed_permanent` (resource_type=
`retention_sweep_cascade`) on three consecutive cascade failures.

**Identity / workspace UUIDs never embedded raw.** Audit metadata holds
`scope_id_hash` = first 16 hex of `SHA-256(uuid_str)` — stable per scope
so operators can correlate two audit rows but unable to reverse without
original UUID.

### Dry-run safety net

With `physical_purge_enabled=False`:

* `retention_physical_purge` runs `physically_purge_expired(dry_run=True)`
  and writes `data.physical_purge` audit with `dry_run: true` + per-table
  `candidates` count.
* `POST /admin/retention/purge/dry-run` is on-demand equivalent —
  always dry-run regardless of flag.

Operators monitor for a couple of weeks, verify magnitude, then flip
`system_settings.retention.physical_purge_enabled = True`.

### Failure handling

* Each per-scope cascade: 3 attempts in-process with exponential
  back-off (0 / 0.5 / 1.5 s).
* Third failure → audit row + watermark advances past bad scope so head
  of queue stays unblocked. Operator must inspect audit, fix cause,
  trigger a manual sweep.
* ARQ `on_job_end` hook also records `job.failed_permanent` if cron
  itself runs out of retries.

Routes:

| Method | Path                                          | Notes                                                          |
|--------|-----------------------------------------------|----------------------------------------------------------------|
| GET    | `/api/v1/admin/retention/watermarks`          | Current cursor for both scope kinds + live `RetentionSettings`. |
| POST   | `/api/v1/admin/retention/sweep/run`           | Enqueue one-off `retention_sweep_cascade` ARQ job.             |
| GET    | `/api/v1/admin/retention/last-runs`           | Most recent `data.cascade_soft_delete` + `data.physical_purge`.|
| POST   | `/api/v1/admin/retention/purge/dry-run`       | Counts only, never deletes regardless of flag.                 |

Cron registration in `app.worker.arq_app.WorkerSettings.cron_jobs`:
`retention_sweep_cascade` every 5 min; `retention_physical_purge` daily
04:00 UTC.
