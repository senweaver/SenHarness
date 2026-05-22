"""Flow execution service — manual, webhook, cron triggers.

Three execution paths:

* ``execution_mode == AGENT`` (default) — full agent loop. Either the
  classic single-prompt path or the visual-DAG ``flow_engine.run_graph``.
* ``execution_mode == NO_AGENT_SCRIPT`` — runs a shell command in the
  workspace's Docker sandbox. Empty stdout = silent (``outcome=success``),
  non-empty stdout = either ``nonempty_output`` (recorded but no agent
  fanout) or ``escalated_to_agent`` based on the flow's escalate flag.
* ``execution_mode == NO_AGENT_HTTP`` — fires a one-shot HTTP probe.
  2xx (or operator-supplied expected statuses) = ``silent_2xx``;
  4xx/5xx → either ``http_error`` or ``escalated_to_agent``.

Both no-agent paths still write a ``FlowRun`` row + audit event so the
operator surface keeps a trail; "silent" only means we don't trigger
notifications / channels / agent loops.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.harness.sandbox import build_sandbox
from app.core.config import settings
from app.core.errors import Conflict, NotFound
from app.core.security import utcnow_naive
from app.core.url_safety import UnsafeURLError, resolve_safe_url
from app.db.models.flow import (
    Flow,
    FlowExecutionMode,
    FlowRun,
    FlowRunOutcome,
    FlowRunStatus,
    FlowTriggerKind,
)
from app.db.models.session import SessionKind
from app.db.session import get_session_factory
from app.repositories.flow import FlowRepository, FlowRunRepository
from app.repositories.session import SessionRepository
from app.schemas.flow import (
    HTTP_BODY_MAX_BYTES,
    HTTP_TIMEOUT_DEFAULT_S,
    SCRIPT_TIMEOUT_DEFAULT_S,
    HttpModeConfig,
    ScriptModeConfig,
)
from app.services import agent_runner as runner
from app.services import audit as audit_svc
from app.services.vault import VaultKeyNotFoundError, resolve_vault_template

log = logging.getLogger(__name__)

# Strong refs to detached background flow-run tasks.
_BACKGROUND_TASKS: set[Any] = set()

_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")
_OUTPUT_EXCERPT_BYTES = 4 * 1024
_HTTP_ERROR_EXCERPT_CHARS = 200
# Sandbox kinds that are safe to use for no-agent shell scripts. ``state``
# has no shell at all; ``local`` runs on the SenHarness backend host so we
# block it in production. The default for new flows is ``docker``.
_SCRIPT_ALLOWED_KINDS = frozenset({"docker", "local"})
_DEFAULT_HTTP_EXPECTED = frozenset(range(200, 300))


def render_prompt(template: str, payload: dict[str, Any]) -> str:
    """Very small ``{{name}}`` substitution — no nested paths beyond dot.

    Missing keys are replaced with an empty string. Keeps the feature tiny
    without pulling in Jinja2.
    """

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        value: Any = payload
        for part in key.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = None
                break
        return "" if value is None else str(value)

    return _TEMPLATE_RE.sub(_sub, template or "")


async def get_or_404(session: AsyncSession, flow_id: uuid.UUID, *, workspace_id: uuid.UUID) -> Flow:
    row = await FlowRepository(session).get(flow_id)
    if row is None or row.workspace_id != workspace_id or row.deleted_at is not None:
        raise NotFound("flow_not_found", code="flow.not_found")
    return row


async def create_flow(
    session: AsyncSession, *, workspace_id: uuid.UUID, created_by: uuid.UUID | None, **kwargs
) -> Flow:
    execution_mode = kwargs.get("execution_mode") or FlowExecutionMode.AGENT
    is_no_agent = execution_mode in (
        FlowExecutionMode.NO_AGENT_SCRIPT,
        FlowExecutionMode.NO_AGENT_HTTP,
    )
    if not is_no_agent:
        # Agent mode still needs a target. Visual-DAG flows can defer to
        # nodes that bind agents themselves, so the top-level FK is optional
        # when ``graph_json`` is non-empty.
        graph = kwargs.get("graph_json") or {}
        has_graph = (
            isinstance(graph, dict)
            and isinstance(graph.get("nodes"), list)
            and len(graph["nodes"]) > 0
        )
        if not has_graph and kwargs.get("agent_id") is None and kwargs.get("squad_id") is None:
            raise Conflict(
                "no_target",
                code="flow.no_target",
                extras={"hint": "Set agent_id, squad_id, or provide a visual graph."},
            )
    return await FlowRepository(session).create(
        workspace_id=workspace_id, created_by=created_by, **kwargs
    )


def _graph_is_active(graph: dict | None) -> bool:
    """Treat a graph as active if it has ≥1 node. Empty dict / empty nodes
    list = fall back to the legacy ``prompt_template`` path."""
    if not graph:
        return False
    nodes = graph.get("nodes")
    return isinstance(nodes, list) and len(nodes) > 0


# ─── No-agent path: helpers ─────────────────────────────────


def _excerpt(text: str | None, *, limit: int) -> str | None:
    """Truncate ``text`` to at most ``limit`` characters, marking truncation
    with a trailing ellipsis. ``None`` / empty input returns ``None`` so
    callers can distinguish "no output" from "empty output"."""
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _is_production() -> bool:
    return str(getattr(settings, "APP_ENV", "")).lower() == "production"


async def _persist_run(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    flow_id: uuid.UUID,
    session_id: uuid.UUID | None,
    trigger_kind: FlowTriggerKind,
    payload: dict[str, Any],
    triggered_by: uuid.UUID | None,
    status: FlowRunStatus,
    outcome: FlowRunOutcome,
    started_at: Any,
    finished_at: Any,
    output_summary: str | None,
    error: str | None,
    probe_response_status: int | None = None,
    probe_duration_ms: int | None = None,
    probe_output_excerpt: str | None = None,
) -> FlowRun:
    run: FlowRun = await FlowRunRepository(db).create(
        workspace_id=workspace_id,
        flow_id=flow_id,
        session_id=session_id,
        trigger_kind=trigger_kind,
        trigger_payload_json=payload or {},
        status=status,
        outcome=outcome,
        started_at=started_at,
        finished_at=finished_at,
        output_summary=output_summary,
        error=error,
        probe_response_status=probe_response_status,
        probe_duration_ms=probe_duration_ms,
        probe_output_excerpt=probe_output_excerpt,
        triggered_by_identity_id=triggered_by,
    )
    return run


async def _stamp_last_run(db: AsyncSession, flow_id: uuid.UUID) -> None:
    flow_row = await FlowRepository(db).get(flow_id)
    if flow_row is not None:
        flow_row.last_run_at = utcnow_naive()
        await db.flush([flow_row])


# ─── Script mode ────────────────────────────────────────────


async def _execute_script(
    *,
    cfg: ScriptModeConfig,
    sandbox_policy: dict[str, Any],
) -> tuple[FlowRunOutcome, int | None, str | None, int]:
    """Run the script and return ``(outcome, exit_code, output, duration_ms)``.

    Pure execution — no DB, no audit. Caller wraps with persistence.
    """
    _cap, backend = build_sandbox(policy=sandbox_policy)
    if backend is None:
        return (
            FlowRunOutcome.SCRIPT_ERROR,
            None,
            "sandbox unavailable (pydantic-ai-backends missing or daemon unreachable)",
            0,
        )

    composed = cfg.script_command
    if cfg.script_cwd:
        # Same convention as the ``shell`` tool: emulate cwd via shell prefix.
        # The ScriptModeConfig validator forbids embedded shell metacharacters
        # in env keys, but the cwd is intentionally permissive (paths with
        # spaces are common); shlex.quote is enough.
        import shlex

        composed = f"cd {shlex.quote(cfg.script_cwd)} && {composed}"
    if cfg.script_env:
        # Inline env vars in front of the command. The validator already
        # rejected dangerous chars in keys; for values, embed via shlex.quote
        # so users with passwords containing spaces are not silently broken.
        import shlex

        prefix = " ".join(f"{k}={shlex.quote(v)}" for k, v in cfg.script_env.items())
        composed = f"{prefix} {composed}"

    timeout_s = int(cfg.script_timeout_s or SCRIPT_TIMEOUT_DEFAULT_S)
    started = time.monotonic()
    try:
        execute = backend.execute
        if asyncio.iscoroutinefunction(execute):
            result = await asyncio.wait_for(
                execute(composed, timeout=timeout_s),
                timeout=timeout_s + 5,
            )
        else:
            loop = asyncio.get_running_loop()
            result = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: execute(composed, timeout=timeout_s)),
                timeout=timeout_s + 5,
            )
    except TimeoutError:
        duration_ms = int((time.monotonic() - started) * 1000)
        return FlowRunOutcome.TIMEOUT, None, None, duration_ms
    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        return FlowRunOutcome.SCRIPT_ERROR, None, str(e)[:_HTTP_ERROR_EXCERPT_CHARS], duration_ms

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout = getattr(result, "stdout", None)
    stderr = getattr(result, "stderr", None)
    output = getattr(result, "output", None)
    if stdout is None and stderr is None and output is not None:
        stdout = output
        stderr = ""
    combined = (stdout or "") + (("\n" + stderr) if stderr else "")
    truncated = combined[-_OUTPUT_EXCERPT_BYTES:] if combined else None

    exit_code = int(getattr(result, "exit_code", 0) or 0)
    if exit_code != 0:
        return FlowRunOutcome.SCRIPT_ERROR, exit_code, truncated, duration_ms
    if not (stdout or "").strip():
        return FlowRunOutcome.SUCCESS, exit_code, truncated, duration_ms
    if cfg.escalate_on_nonempty_output:
        return FlowRunOutcome.ESCALATED_TO_AGENT, exit_code, truncated, duration_ms
    return FlowRunOutcome.NONEMPTY_OUTPUT, exit_code, truncated, duration_ms


def _resolve_script_sandbox_policy(flow: Flow) -> dict[str, Any]:
    """Pick the sandbox policy for a script flow.

    Honors the flow's ``metadata_json.sandbox`` block when present; falls
    back to ``{"sandbox": {"kind": "docker"}}`` because that's the only
    kind safe in production. The validator below rejects ``local`` in
    production with a stable error code.
    """
    md = flow.metadata_json or {}
    if isinstance(md.get("sandbox"), (dict, str)):
        return {"sandbox": md["sandbox"], "session_id": str(flow.id)}
    return {"sandbox": {"kind": "docker"}, "session_id": str(flow.id)}


def _validate_script_sandbox_policy(policy: dict[str, Any]) -> tuple[bool, str | None]:
    """Returns ``(ok, error_code)``. Code is set when production refuses."""
    spec = policy.get("sandbox")
    if isinstance(spec, dict):
        kind = str(spec.get("kind", "local")).lower()
    elif isinstance(spec, str):
        kind = spec.lower()
    else:
        kind = "local"
    if kind not in _SCRIPT_ALLOWED_KINDS:
        return False, "flow.script_sandbox_unsupported_kind"
    if kind == "local" and _is_production():
        return False, "flow.script_local_blocked"
    return True, None


async def _run_script_flow(
    flow: Flow,
    *,
    trigger_kind: FlowTriggerKind,
    payload: dict[str, Any],
    triggered_by: uuid.UUID | None,
) -> uuid.UUID:
    """Execute a no-agent script flow and persist its FlowRun."""
    factory = get_session_factory()

    cfg_dict = flow.trigger_config or {}
    try:
        cfg = ScriptModeConfig.model_validate(cfg_dict)
    except Exception as e:
        async with factory() as db:
            run = await _persist_run(
                db,
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                session_id=None,
                trigger_kind=trigger_kind,
                payload=payload,
                triggered_by=triggered_by,
                status=FlowRunStatus.FAILED,
                outcome=FlowRunOutcome.VALIDATION_FAILED,
                started_at=utcnow_naive(),
                finished_at=utcnow_naive(),
                output_summary=None,
                error=str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
            )
            await audit_svc.record(
                db,
                action="flow.validation_failed",
                actor_identity_id=triggered_by,
                workspace_id=flow.workspace_id,
                resource_type="flow",
                resource_id=flow.id,
                summary=f"script validation failed for flow {flow.name!r}",
                metadata={"flow_id": str(flow.id), "run_id": str(run.id)},
            )
            await db.commit()
            return run.id

    sandbox_policy = _resolve_script_sandbox_policy(flow)
    ok, err_code = _validate_script_sandbox_policy(sandbox_policy)
    if not ok:
        async with factory() as db:
            run = await _persist_run(
                db,
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                session_id=None,
                trigger_kind=trigger_kind,
                payload=payload,
                triggered_by=triggered_by,
                status=FlowRunStatus.FAILED,
                outcome=FlowRunOutcome.VALIDATION_FAILED,
                started_at=utcnow_naive(),
                finished_at=utcnow_naive(),
                output_summary=None,
                error=err_code,
            )
            await audit_svc.record(
                db,
                action="flow.script_local_blocked",
                actor_identity_id=triggered_by,
                workspace_id=flow.workspace_id,
                resource_type="flow",
                resource_id=flow.id,
                summary=f"script flow {flow.name!r} blocked by sandbox guard ({err_code})",
                metadata={"flow_id": str(flow.id), "run_id": str(run.id), "code": err_code},
            )
            await db.commit()
            return run.id

    started_at = utcnow_naive()
    outcome, exit_code, output_excerpt, duration_ms = await _execute_script(
        cfg=cfg, sandbox_policy=sandbox_policy
    )
    finished_at = utcnow_naive()

    if outcome == FlowRunOutcome.ESCALATED_TO_AGENT:
        return await _escalate_to_agent(
            flow,
            trigger_kind=trigger_kind,
            triggered_by=triggered_by,
            payload=payload,
            escalation_context={
                "source": "script",
                "exit_code": exit_code,
                "stdout_excerpt": output_excerpt,
                "duration_ms": duration_ms,
            },
            probe_status=None,
            probe_duration_ms=duration_ms,
            probe_output_excerpt=output_excerpt,
        )

    status = (
        FlowRunStatus.SUCCEEDED
        if outcome in (FlowRunOutcome.SUCCESS, FlowRunOutcome.NONEMPTY_OUTPUT)
        else FlowRunStatus.FAILED
    )
    error_msg = None
    if outcome == FlowRunOutcome.SCRIPT_ERROR:
        error_msg = f"exit_code={exit_code}" if exit_code is not None else "script error"
    elif outcome == FlowRunOutcome.TIMEOUT:
        error_msg = f"timeout after {cfg.script_timeout_s}s"

    async with factory() as db:
        run = await _persist_run(
            db,
            workspace_id=flow.workspace_id,
            flow_id=flow.id,
            session_id=None,
            trigger_kind=trigger_kind,
            payload=payload,
            triggered_by=triggered_by,
            status=status,
            outcome=outcome,
            started_at=started_at,
            finished_at=finished_at,
            output_summary=_excerpt(output_excerpt, limit=1000),
            error=error_msg,
            probe_duration_ms=duration_ms,
            probe_output_excerpt=output_excerpt,
        )
        await audit_svc.record(
            db,
            action="flow.script_executed",
            actor_identity_id=triggered_by,
            workspace_id=flow.workspace_id,
            resource_type="flow",
            resource_id=flow.id,
            summary=f"script flow {flow.name!r}: outcome={outcome.value}",
            metadata={
                "flow_id": str(flow.id),
                "run_id": str(run.id),
                "outcome": outcome.value,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_excerpt_chars": len(output_excerpt or ""),
            },
        )
        await _stamp_last_run(db, flow.id)
        await db.commit()
        return run.id


# ─── HTTP mode ──────────────────────────────────────────────


async def _resolve_http_payload(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    cfg: HttpModeConfig,
) -> tuple[dict[str, str], str | None]:
    """Resolve vault templates in headers + body. Workspace-scoped."""
    headers: dict[str, str] = {}
    for k, v in cfg.http_headers.items():
        headers[k] = await resolve_vault_template(db, workspace_id=workspace_id, template=v)
    body = None
    if cfg.http_method == "POST" and cfg.http_body:
        body = await resolve_vault_template(db, workspace_id=workspace_id, template=cfg.http_body)
        if body and len(body.encode("utf-8")) > HTTP_BODY_MAX_BYTES:
            raise ValueError("http_body exceeds 64 KiB after vault expansion")
    return headers, body


async def _execute_http(
    *,
    cfg: HttpModeConfig,
    headers: dict[str, str],
    body: str | None,
    pinned_url: str,
    pinned_ip: str | None,
    host_header: str,
) -> tuple[FlowRunOutcome, int | None, str | None, int]:
    """Send the probe; return ``(outcome, status, error_excerpt, duration_ms)``."""
    expected = (
        frozenset(cfg.http_expected_status) if cfg.http_expected_status else _DEFAULT_HTTP_EXPECTED
    )
    timeout_s = float(cfg.http_timeout_s or HTTP_TIMEOUT_DEFAULT_S)
    started = time.monotonic()
    final_headers = dict(headers)
    # Even when we connect to a pinned IP, the Host header drives any
    # downstream virtual-host routing. Pinning is a defence vs DNS
    # rebinding only — the server-side identity never changes.
    final_headers.setdefault("Host", host_header)

    request_url = pinned_url
    if pinned_ip and pinned_ip not in pinned_url:
        # Swap the hostname for the pinned IP. ``pinned_url`` already has
        # a literal IP when the original URL did, so we only rewrite when
        # the pin was DNS-derived.
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(pinned_url)
        netloc_ip = pinned_ip
        if parsed.port is not None:
            netloc_ip = f"{pinned_ip}:{parsed.port}"
        # IPv6 literals must be wrapped in brackets in the URL netloc.
        if ":" in pinned_ip and not pinned_ip.startswith("["):
            netloc_ip = f"[{pinned_ip}]:{parsed.port}" if parsed.port else f"[{pinned_ip}]"
        request_url = urlunparse(parsed._replace(netloc=netloc_ip))

    try:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=False,
            verify=True,
        ) as client:
            method = cfg.http_method
            if method == "POST":
                response = await client.request(
                    method,
                    request_url,
                    headers=final_headers,
                    content=body or b"",
                )
            else:
                response = await client.request(method, request_url, headers=final_headers)
    except httpx.TimeoutException:
        duration_ms = int((time.monotonic() - started) * 1000)
        return FlowRunOutcome.TIMEOUT, None, None, duration_ms
    except Exception as e:
        duration_ms = int((time.monotonic() - started) * 1000)
        excerpt = str(e)[:_HTTP_ERROR_EXCERPT_CHARS]
        return FlowRunOutcome.HTTP_ERROR, None, excerpt, duration_ms

    duration_ms = int((time.monotonic() - started) * 1000)
    status = response.status_code
    if status in expected:
        return FlowRunOutcome.SILENT_2XX, status, None, duration_ms
    if cfg.escalate_on_http_failure:
        return FlowRunOutcome.ESCALATED_TO_AGENT, status, None, duration_ms
    return FlowRunOutcome.HTTP_ERROR, status, None, duration_ms


async def _run_http_flow(
    flow: Flow,
    *,
    trigger_kind: FlowTriggerKind,
    payload: dict[str, Any],
    triggered_by: uuid.UUID | None,
) -> uuid.UUID:
    factory = get_session_factory()
    cfg_dict = flow.trigger_config or {}

    try:
        cfg = HttpModeConfig.model_validate(cfg_dict)
    except Exception as e:
        async with factory() as db:
            run = await _persist_run(
                db,
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                session_id=None,
                trigger_kind=trigger_kind,
                payload=payload,
                triggered_by=triggered_by,
                status=FlowRunStatus.FAILED,
                outcome=FlowRunOutcome.VALIDATION_FAILED,
                started_at=utcnow_naive(),
                finished_at=utcnow_naive(),
                output_summary=None,
                error=str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
            )
            await audit_svc.record(
                db,
                action="flow.validation_failed",
                actor_identity_id=triggered_by,
                workspace_id=flow.workspace_id,
                resource_type="flow",
                resource_id=flow.id,
                summary=f"http validation failed for flow {flow.name!r}",
                metadata={"flow_id": str(flow.id), "run_id": str(run.id)},
            )
            await db.commit()
            return run.id

    try:
        url, pinned_ip = resolve_safe_url(cfg.http_url)
    except UnsafeURLError as e:
        from urllib.parse import urlparse

        host = urlparse(cfg.http_url).hostname or "?"
        async with factory() as db:
            run = await _persist_run(
                db,
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                session_id=None,
                trigger_kind=trigger_kind,
                payload=payload,
                triggered_by=triggered_by,
                status=FlowRunStatus.FAILED,
                outcome=FlowRunOutcome.SSRF_BLOCKED,
                started_at=utcnow_naive(),
                finished_at=utcnow_naive(),
                output_summary=None,
                error=str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
            )
            await audit_svc.record(
                db,
                action="flow.http_ssrf_blocked",
                actor_identity_id=triggered_by,
                workspace_id=flow.workspace_id,
                resource_type="flow",
                resource_id=flow.id,
                summary=f"http flow {flow.name!r} rejected by SSRF guard",
                metadata={
                    "flow_id": str(flow.id),
                    "run_id": str(run.id),
                    "code": e.code,
                    "host": host,
                },
            )
            await db.commit()
            return run.id

    async with factory() as db:
        try:
            headers, body = await _resolve_http_payload(db, workspace_id=flow.workspace_id, cfg=cfg)
        except (VaultKeyNotFoundError, ValueError) as e:
            run = await _persist_run(
                db,
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                session_id=None,
                trigger_kind=trigger_kind,
                payload=payload,
                triggered_by=triggered_by,
                status=FlowRunStatus.FAILED,
                outcome=FlowRunOutcome.VALIDATION_FAILED,
                started_at=utcnow_naive(),
                finished_at=utcnow_naive(),
                output_summary=None,
                error=str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
            )
            await audit_svc.record(
                db,
                action="flow.validation_failed",
                actor_identity_id=triggered_by,
                workspace_id=flow.workspace_id,
                resource_type="flow",
                resource_id=flow.id,
                summary=f"http flow {flow.name!r} vault resolve failed",
                metadata={"flow_id": str(flow.id), "run_id": str(run.id)},
            )
            await db.commit()
            return run.id

    from urllib.parse import urlparse

    host_header = urlparse(url).hostname or ""
    started_at = utcnow_naive()
    outcome, status, error_excerpt, duration_ms = await _execute_http(
        cfg=cfg,
        headers=headers,
        body=body,
        pinned_url=url,
        pinned_ip=pinned_ip,
        host_header=host_header,
    )
    finished_at = utcnow_naive()

    if outcome == FlowRunOutcome.ESCALATED_TO_AGENT:
        return await _escalate_to_agent(
            flow,
            trigger_kind=trigger_kind,
            triggered_by=triggered_by,
            payload=payload,
            escalation_context={
                "source": "http",
                "status": status,
                "duration_ms": duration_ms,
            },
            probe_status=status,
            probe_duration_ms=duration_ms,
            probe_output_excerpt=None,
        )

    run_status = (
        FlowRunStatus.SUCCEEDED if outcome == FlowRunOutcome.SILENT_2XX else FlowRunStatus.FAILED
    )
    error_msg = None
    if outcome == FlowRunOutcome.HTTP_ERROR:
        error_msg = (
            error_excerpt
            if error_excerpt
            else (f"unexpected status {status}" if status else "http error")
        )
    elif outcome == FlowRunOutcome.TIMEOUT:
        error_msg = f"timeout after {cfg.http_timeout_s}s"

    async with factory() as db:
        run = await _persist_run(
            db,
            workspace_id=flow.workspace_id,
            flow_id=flow.id,
            session_id=None,
            trigger_kind=trigger_kind,
            payload=payload,
            triggered_by=triggered_by,
            status=run_status,
            outcome=outcome,
            started_at=started_at,
            finished_at=finished_at,
            output_summary=None,
            error=error_msg,
            probe_response_status=status,
            probe_duration_ms=duration_ms,
            probe_output_excerpt=None,
        )
        await audit_svc.record(
            db,
            action="flow.http_executed",
            actor_identity_id=triggered_by,
            workspace_id=flow.workspace_id,
            resource_type="flow",
            resource_id=flow.id,
            summary=f"http flow {flow.name!r}: outcome={outcome.value}",
            metadata={
                "flow_id": str(flow.id),
                "run_id": str(run.id),
                "outcome": outcome.value,
                "status": status,
                "duration_ms": duration_ms,
            },
        )
        await _stamp_last_run(db, flow.id)
        await db.commit()
        return run.id


# ─── Agent path (unchanged) + escalation bridge ─────────────


async def _escalate_to_agent(
    flow: Flow,
    *,
    trigger_kind: FlowTriggerKind,
    triggered_by: uuid.UUID | None,
    payload: dict[str, Any],
    escalation_context: dict[str, Any],
    probe_status: int | None,
    probe_duration_ms: int | None,
    probe_output_excerpt: str | None,
) -> uuid.UUID:
    """Bridge a no-agent probe back into the agent loop.

    Records audit + a FlowRun with ``outcome=escalated_to_agent`` so the
    user surface can colour it distinctly, then enqueues the existing
    agent path with an ``escalation_context`` field on the payload so
    the agent's prompt template can reference it.
    """
    factory = get_session_factory()
    enriched_payload = {**payload, "escalation_context": escalation_context}

    if flow.agent_id is None and not _graph_is_active(flow.graph_json):
        # No agent target — record the escalation but stop here. The
        # operator misconfigured the flow; we still write a FlowRun so it
        # surfaces in the runs list rather than vanishing silently.
        async with factory() as db:
            run = await _persist_run(
                db,
                workspace_id=flow.workspace_id,
                flow_id=flow.id,
                session_id=None,
                trigger_kind=trigger_kind,
                payload=enriched_payload,
                triggered_by=triggered_by,
                status=FlowRunStatus.FAILED,
                outcome=FlowRunOutcome.VALIDATION_FAILED,
                started_at=utcnow_naive(),
                finished_at=utcnow_naive(),
                output_summary=None,
                error="escalation requested but flow has no agent / graph target",
                probe_response_status=probe_status,
                probe_duration_ms=probe_duration_ms,
                probe_output_excerpt=probe_output_excerpt,
            )
            await audit_svc.record(
                db,
                action="flow.validation_failed",
                actor_identity_id=triggered_by,
                workspace_id=flow.workspace_id,
                resource_type="flow",
                resource_id=flow.id,
                summary=f"escalation failed: flow {flow.name!r} has no target",
                metadata={"flow_id": str(flow.id), "run_id": str(run.id)},
            )
            await db.commit()
            return run.id

    async with factory() as db:
        new_session = await SessionRepository(db).create(
            workspace_id=flow.workspace_id,
            kind=SessionKind.P2P,
            subject_id=flow.agent_id,
            title=f"[flow] {flow.name}",
            metadata_json={
                "flow_id": str(flow.id),
                "trigger": trigger_kind,
                "escalated_from": escalation_context.get("source"),
            },
        )
        run = await _persist_run(
            db,
            workspace_id=flow.workspace_id,
            flow_id=flow.id,
            session_id=new_session.id,
            trigger_kind=trigger_kind,
            payload=enriched_payload,
            triggered_by=triggered_by,
            status=FlowRunStatus.RUNNING,
            outcome=FlowRunOutcome.ESCALATED_TO_AGENT,
            started_at=utcnow_naive(),
            finished_at=None,
            output_summary=None,
            error=None,
            probe_response_status=probe_status,
            probe_duration_ms=probe_duration_ms,
            probe_output_excerpt=probe_output_excerpt,
        )
        await audit_svc.record(
            db,
            action="flow.escalated_to_agent",
            actor_identity_id=triggered_by,
            workspace_id=flow.workspace_id,
            resource_type="flow",
            resource_id=flow.id,
            summary=f"flow {flow.name!r} escalated to agent",
            metadata={
                "flow_id": str(flow.id),
                "run_id": str(run.id),
                "source": escalation_context.get("source"),
            },
        )
        await db.commit()

        run_id = run.id
        ws_id = flow.workspace_id
        agent_id = flow.agent_id
        session_id = new_session.id
        graph = dict(flow.graph_json or {}) if _graph_is_active(flow.graph_json) else None
        prompt = (
            None if graph is not None else render_prompt(flow.prompt_template, enriched_payload)
        )

    _spawn_agent_run_task(
        run_id=run_id,
        flow_id=flow.id,
        ws_id=ws_id,
        agent_id=agent_id,
        session_id=session_id,
        graph=graph,
        prompt=prompt,
        payload=enriched_payload,
        triggered_by=triggered_by,
    )
    return run_id


def _spawn_agent_run_task(
    *,
    run_id: uuid.UUID,
    flow_id: uuid.UUID,
    ws_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    session_id: uuid.UUID,
    graph: dict | None,
    prompt: str | None,
    payload: dict[str, Any],
    triggered_by: uuid.UUID | None,
) -> None:
    async def _execute() -> None:
        try:
            if graph is not None:
                from app.services import flow_engine

                await flow_engine.run_graph(
                    flow_run_id=run_id,
                    graph=graph,
                    trigger_payload=payload,
                    workspace_id=ws_id,
                    session_id=session_id,
                    identity_id=triggered_by,
                )
            else:
                factory2 = get_session_factory()
                async with factory2() as db2:
                    result = await runner.run_agent_one_shot(
                        db2,
                        workspace_id=ws_id,
                        agent_id=agent_id,
                        session_id=session_id,
                        identity_id=triggered_by,
                        user_text=prompt or "",
                    )
                    run_row = await FlowRunRepository(db2).get(run_id)
                    if run_row is not None:
                        run_row.status = (
                            FlowRunStatus.SUCCEEDED
                            if result.error is None
                            else FlowRunStatus.FAILED
                        )
                        run_row.finished_at = utcnow_naive()
                        run_row.output_summary = (result.final_text or "")[:1000]
                        run_row.error = result.error
                        await db2.flush([run_row])
                    await db2.commit()

            factory3 = get_session_factory()
            async with factory3() as db3:
                await _stamp_last_run(db3, flow_id)
                await db3.commit()
        except Exception as e:  # pragma: no cover
            log.exception("flow run failed")
            factory_err = get_session_factory()
            async with factory_err() as db_err:
                run_row = await FlowRunRepository(db_err).get(run_id)
                if run_row is not None:
                    run_row.status = FlowRunStatus.FAILED
                    run_row.finished_at = utcnow_naive()
                    run_row.error = str(e)[:1000]
                    await db_err.flush([run_row])
                    await db_err.commit()

    task = asyncio.create_task(_execute())
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


async def trigger_flow(
    flow_id: uuid.UUID,
    *,
    workspace_id: uuid.UUID,
    trigger_kind: FlowTriggerKind,
    payload: dict[str, Any] | None = None,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Fire a flow — returns the new FlowRun id.

    Picks one of three execution paths based on ``flow.execution_mode``:

    * ``no_agent_script`` → :func:`_run_script_flow`
    * ``no_agent_http``   → :func:`_run_http_flow`
    * ``agent`` (default) → original visual-DAG / classic single-prompt path

    Opens its own DB session so this can be called from an APScheduler job.
    """
    factory = get_session_factory()
    payload = payload or {}
    async with factory() as db:
        flow = await FlowRepository(db).get(flow_id)
        if flow is None or flow.deleted_at is not None:
            raise NotFound("flow_not_found", code="flow.not_found")
        if flow.workspace_id != workspace_id:
            raise NotFound("flow_not_found", code="flow.not_found")
        if not flow.enabled:
            raise Conflict("flow_disabled", code="flow.disabled")

        execution_mode = flow.execution_mode

    if execution_mode == FlowExecutionMode.NO_AGENT_SCRIPT:
        return await _run_script_flow(
            flow,
            trigger_kind=trigger_kind,
            payload=payload,
            triggered_by=triggered_by,
        )
    if execution_mode == FlowExecutionMode.NO_AGENT_HTTP:
        return await _run_http_flow(
            flow,
            trigger_kind=trigger_kind,
            payload=payload,
            triggered_by=triggered_by,
        )

    return await _run_agent_flow(
        flow_id=flow_id,
        trigger_kind=trigger_kind,
        payload=payload,
        triggered_by=triggered_by,
    )


async def _run_agent_flow(
    *,
    flow_id: uuid.UUID,
    trigger_kind: FlowTriggerKind,
    payload: dict[str, Any],
    triggered_by: uuid.UUID | None,
) -> uuid.UUID:
    """Original agent-path — one-shot or visual graph."""
    factory = get_session_factory()
    async with factory() as db:
        flow = await FlowRepository(db).get(flow_id)
        if flow is None or flow.deleted_at is not None:
            raise NotFound("flow_not_found", code="flow.not_found")
        uses_graph = _graph_is_active(flow.graph_json)
        if not uses_graph and flow.agent_id is None:
            raise Conflict("squad_flow_not_supported", code="flow.squad_not_supported")

        new_session = await SessionRepository(db).create(
            workspace_id=flow.workspace_id,
            kind=SessionKind.P2P,
            subject_id=flow.agent_id,
            title=f"[flow] {flow.name}",
            metadata_json={"flow_id": str(flow.id), "trigger": trigger_kind},
        )
        fr: FlowRun = await FlowRunRepository(db).create(
            workspace_id=flow.workspace_id,
            flow_id=flow.id,
            session_id=new_session.id,
            trigger_kind=trigger_kind,
            trigger_payload_json=payload,
            status=FlowRunStatus.RUNNING,
            outcome=FlowRunOutcome.PENDING,
            started_at=utcnow_naive(),
            triggered_by_identity_id=triggered_by,
        )
        await db.commit()

        run_id = fr.id
        ws_id = flow.workspace_id
        agent_id = flow.agent_id
        session_id = new_session.id
        graph = dict(flow.graph_json or {}) if uses_graph else None
        prompt = None if uses_graph else render_prompt(flow.prompt_template, payload)

    _spawn_agent_run_task(
        run_id=run_id,
        flow_id=flow_id,
        ws_id=ws_id,
        agent_id=agent_id,
        session_id=session_id,
        graph=graph,
        prompt=prompt,
        payload=payload,
        triggered_by=triggered_by,
    )
    return run_id


# ─── Dry-run test endpoints ─────────────────────────────────


async def dry_run_script(
    flow: Flow, *, override_config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute the script flow in dry-run — no FlowRun row, no audit.

    Returns a dict shaped like ``FlowTestResult``; the API layer wraps it.
    """
    cfg_dict = override_config if override_config is not None else (flow.trigger_config or {})
    try:
        cfg = ScriptModeConfig.model_validate(cfg_dict)
    except Exception as e:
        return {
            "outcome": FlowRunOutcome.VALIDATION_FAILED,
            "duration_ms": 0,
            "error": str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
        }

    sandbox_policy = _resolve_script_sandbox_policy(flow)
    ok, err_code = _validate_script_sandbox_policy(sandbox_policy)
    if not ok:
        return {
            "outcome": FlowRunOutcome.VALIDATION_FAILED,
            "duration_ms": 0,
            "error": err_code,
        }

    outcome, exit_code, output_excerpt, duration_ms = await _execute_script(
        cfg=cfg, sandbox_policy=sandbox_policy
    )
    error_msg: str | None = None
    if outcome == FlowRunOutcome.SCRIPT_ERROR:
        error_msg = (
            output_excerpt
            if output_excerpt
            else (f"exit_code={exit_code}" if exit_code is not None else "script error")
        )
    elif outcome == FlowRunOutcome.TIMEOUT:
        error_msg = f"timeout after {cfg.script_timeout_s}s"
    return {
        "outcome": outcome,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "output_excerpt": output_excerpt,
        "error": error_msg,
    }


async def dry_run_http(
    db: AsyncSession,
    flow: Flow,
    *,
    override_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg_dict = override_config if override_config is not None else (flow.trigger_config or {})
    try:
        cfg = HttpModeConfig.model_validate(cfg_dict)
    except Exception as e:
        return {
            "outcome": FlowRunOutcome.VALIDATION_FAILED,
            "duration_ms": 0,
            "error": str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
        }
    try:
        url, pinned_ip = resolve_safe_url(cfg.http_url)
    except UnsafeURLError as e:
        return {
            "outcome": FlowRunOutcome.SSRF_BLOCKED,
            "duration_ms": 0,
            "error": e.code,
        }
    try:
        headers, body = await _resolve_http_payload(db, workspace_id=flow.workspace_id, cfg=cfg)
    except (VaultKeyNotFoundError, ValueError) as e:
        return {
            "outcome": FlowRunOutcome.VALIDATION_FAILED,
            "duration_ms": 0,
            "error": str(e)[:_HTTP_ERROR_EXCERPT_CHARS],
        }
    from urllib.parse import urlparse

    host_header = urlparse(url).hostname or ""
    outcome, status, error_excerpt, duration_ms = await _execute_http(
        cfg=cfg,
        headers=headers,
        body=body,
        pinned_url=url,
        pinned_ip=pinned_ip,
        host_header=host_header,
    )
    return {
        "outcome": outcome,
        "duration_ms": duration_ms,
        "response_status": status,
        "error": error_excerpt,
    }
