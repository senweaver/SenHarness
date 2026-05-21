# Adding features

How-to for the most common extension points. Each section is a recipe
plus pointers to the existing code that exercises the same pattern.

## New REST endpoint

Layered checklist (the [patterns.md](patterns.md) order):

1. **Schema** in `app/schemas/<module>.py` — `Create` / `Update` /
   `Read` Pydantic models. Never reuse ORM rows on the wire.
2. **Repository method** on the right `AsyncRepository` subclass in
   `app/repositories/<entity>.py`. Add a `.flush()` if you mutated; do
   not commit here.
3. **Service** in `app/services/<module>.py` — owns the transaction.
   Enforces workspace scoping + permissions. Raises typed errors
   (`NotFoundError`, `AlreadyExistsError`, etc.).
4. **Route** in `app/api/v1/<module>.py`, **registered in
   `app/api/router.py`**. Thin handler: parse, authorize via
   `Depends(get_current_identity)` / `ensure_admin(...)`, delegate to
   service, return `*Read` schema.
5. **Permissions** — extend `app/services/permissions.py` if a new
   action exists. RBAC is workspace-member / workspace-admin /
   workspace-owner / platform-admin (last via
   `identity.platform_role`).
6. **Tests** — unit for the service, integration for the route happy
   path + at least one auth-failure case + one workspace-mismatch case
   (asserts 404 not 403).
7. **Migration** — if you touched ORM models, `make migration m="feat:
   <description>"` and review the diff before commit (autogen misses
   enums, server defaults, index renames; see
   [patterns.md#migrations](patterns.md#migrations)).

Reference pattern:
[`backend/app/api/v1/session_goals.py`](../backend/app/api/v1/session_goals.py)
+ [`backend/app/services/session_goal.py`](../backend/app/services/session_goal.py).

## New tool

A tool the agent loop can invoke. Lives under `app/agents/tools/<name>.py`.

```python
# app/agents/tools/my_tool.py
from pydantic import BaseModel, Field
from app.agents.tools._registry import BuiltinTool

class MyToolArgs(BaseModel):
    target: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)

async def my_tool_runner(ctx, args: MyToolArgs) -> dict:
    # ctx exposes workspace_id, agent_id, session_id, run_id, identity_id.
    # All side effects scoped to ctx.workspace_id.
    return {"status": "ok", "results": [...]}

MY_TOOL = BuiltinTool(
    name="my_tool",
    description="One-sentence description.",
    args_model=MyToolArgs,
    runner=my_tool_runner,
    available_for_kinds=None,           # None = workspace agents. ("evolver",) = evolver only.
)
```

Add it to `BUILTIN_TOOL_REGISTRY` in
[`app/agents/tools/__init__.py`](../backend/app/agents/tools/__init__.py).
The runner picks it up next boot; no schema change.

Shared per-run state goes in
[`_context.py`](../backend/app/agents/tools/_context.py) — never module
globals.

## Agent Runtime adapter

For plugging your own engine (CrewAI, AutoGen, local Llama wrapper) into
SenHarness. Three methods on `AgentBackend`:

```python
# app/agents/kernels/echo/adapter.py
from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from app.agents.kernels.base import (
    AgentBackend, BackendCapabilities, RunEvent, RunEventKind, RunRequest,
)


class EchoBackend(AgentBackend):
    backend_kind = "echo"

    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_streaming=True,
            display_name="Echo (demo)",
            description="Echoes the user message one character at a time.",
        )

    async def run(self, req: RunRequest) -> AsyncIterator[RunEvent]:
        for char in req.user_text:
            await asyncio.sleep(0.02)
            yield RunEvent(RunEventKind.DELTA, {"text": char})
        yield RunEvent(
            RunEventKind.FINAL,
            {"message_id": str(uuid.uuid4()), "summary": None},
        )

    async def cancel(self, run_id: uuid.UUID) -> None:
        # Echo is stateless; cancellation propagates via the caller
        # cancelling the outer async generator.
        return
```

Register on startup:

```python
# app/agents/kernels/echo/__init__.py
from app.agents.kernels.echo.adapter import EchoBackend
from app.agents.kernels.registry import register

register(EchoBackend())
```

Import once from `app/main.py` lifespan so the registration fires:

```python
import app.agents.kernels.echo  # noqa: F401
```

Create an agent with `backend_kind="echo"` and chat — every character
streams back through the normal WS pipeline.

For full payload + event vocabulary + tenancy rules see
[extensions-and-governance.md#agent-runtime-adapters](extensions-and-governance.md#agent-runtime-adapters).

## Channel provider

Bridges an IM platform to agents. One class subclassing
`ChannelProvider`, registered in
`app/services/channels/__init__.py`:

```python
# app/services/channels/slack_simple.py
import httpx
from app.services.channels.base import (
    ChannelProvider, ChannelProviderMeta, InboundMessage,
)


class SlackSimpleProvider(ChannelProvider):
    kind = "slack_simple"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Slack (simple)",
            description="Slack without HMAC — trusts the shared inbound token.",
            required_config_fields=("bot_token",),
            optional_config_fields=("team_id",),
            supports_outbound=True,
        )

    def parse_inbound(self, payload, headers):
        event = (payload or {}).get("event") or {}
        if event.get("type") != "message":
            return None
        text = (event.get("text") or "").strip()
        if not text:
            return None
        return InboundMessage(
            thread_key=event.get("channel", "fallback"),
            user_text=text,
            external_user=event.get("user", "unknown"),
            raw=payload,
        )

    async def post_reply(self, *, channel_config, thread_key, text):
        token = channel_config.get("bot_token")
        if not token:
            return
        async with httpx.AsyncClient(timeout=10.0) as cli:
            await cli.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {token}"},
                json={"channel": thread_key, "text": text},
            )
```

Register:

```python
# app/services/channels/__init__.py
from app.services.channels.slack_simple import SlackSimpleProvider

register_provider(SlackSimpleProvider())
```

Once registered, operators pick the provider from
`/settings/channels`, configure required fields (auto-masked for
secret-like names), and point the IM platform's events URL at
`https://<host>/api/v1/hooks/ingress/<channel_id>` with
`X-Senharness-Token: <inbound_token>`.

For signature verification, handshake responses, and stream-mode wiring
(`feishu` / `dingtalk` / `discord` / `wecom` / `qq` / `wechat`), see
[extensions-and-governance.md#channel-providers](extensions-and-governance.md#channel-providers).

## KB connector

For ingesting documents from a new source kind (Confluence, Notion,
SharePoint). One file, one `register_connector(...)` call:

```python
# app/services/kb_connectors/confluence.py
from app.services.kb_connectors.base import (
    ConnectorDocument, ConnectorMeta, KbConnector, SyncProgressEvent,
)
from app.services.kb_connectors import register_connector
from app.db.models.knowledge import DocSourceKind


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
        # ... paginate the Confluence API ...
        yield ConnectorDocument(
            title=page["title"],
            source_kind=DocSourceKind.URL,
            source_uri=page["url"],
            raw_text=page["body"],
            external_id=page["id"],
            metadata={"connector_kind": self.kind, "space": config["space_key"]},
        )


register_connector(ConfluenceConnector())
```

Import the module once from app startup. `kb_sources.kind` is
`String(32)` — no migration needed.

## Background task (ARQ)

For recurring sweeps and async post-processing:

```python
# app/jobs/my_sweep.py
from app.db.session import get_session_factory
from app.services.audit import audit_svc

async def my_workspace_sweep(ctx: dict) -> dict:
    # ctx is the ARQ context (redis pool, job_id, etc.)
    session_factory = get_session_factory()
    workspaces_seen = 0
    async with session_factory() as db:
        # iterate non-deleted workspaces
        async for workspace in iter_workspaces(db):
            try:
                await do_work(db, workspace_id=workspace.id)
                workspaces_seen += 1
            except Exception:
                # per-workspace exception isolated + audited; sweep continues
                log.exception("my_sweep: workspace=%s", workspace.id)
                await audit_svc.record(
                    db, action="my.sweep_failed",
                    workspace_id=workspace.id, ...
                )
        await db.commit()
    return {"workspaces_seen": workspaces_seen}


async def on_my_sweep_job_failed_permanent(ctx, exc):
    # called after 3 strikes
    ...
```

Register in
[`app/worker/arq_app.py`](../backend/app/worker/arq_app.py):

```python
WorkerSettings.functions.append(my_workspace_sweep)
WorkerSettings.cron_jobs.append(
    cron(my_workspace_sweep, minute={6, 36})   # pick a free slot
)
```

Cron slot conflicts: see the slot map in
[runtime-and-jobs.md#cron-slot-map](runtime-and-jobs.md#cron-slot-map).
Pick a minute or hour that no other job already uses.

Three-strike permanent failure hook:

```python
WorkerSettings.on_job_end_hooks.append(
    job_end_hook(
        function_name="my_workspace_sweep",
        on_failed_permanent=on_my_sweep_job_failed_permanent,
    )
)
```

## New auth flow

OAuth provider, MFA channel, registration source kind — these all live
under `app/services/auth/` + `app/api/v1/auth.py`. The platform-settings
sections `auth.registration` / `auth.oauth` / `auth.mfa` already cover
the configuration shape; you mostly add a new branch in the dispatch
helper plus a schema entry.

For OAuth: add a provider config to `OAuthProviderSettings`, implement
the discovery + callback handler in `app/services/auth/oauth_<provider>.py`,
register the route in `app/api/v1/auth.py`. The
`OAuthDispatcher.dispatch()` switch picks up the new provider.

## What NOT to add

* A new top-level `docs/<feature>.md` — edit the closest existing
  thematic file (`skills.md` / `runtime-and-jobs.md` /
  `extensions-and-governance.md`) instead. See
  [the AGENTS.md Docs hygiene rule](../AGENTS.md).
* A new milestone changelog file in `docs/changelog/` — this folder is
  gone. Milestone summaries go in PR descriptions; git history is the
  source of truth.
* An ad-hoc retry / timeout in a route handler — push it into
  `app/agents/harness/reliability.py` or
  `app/agents/harness/` (L3–L6 helpers).
* A direct `pydantic_ai.Agent` call inside a run — go through
  `AgentBackend`.
* A `fetch()` from a frontend component — go through
  [`frontend/src/lib/api.ts`](../frontend/src/lib/api.ts).
