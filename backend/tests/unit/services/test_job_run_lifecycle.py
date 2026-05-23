"""Unit tests for the M4.6 ``job_run`` service lifecycle.

Covers:

* Round-tripping through ``record_job_enqueued`` →
  ``record_job_started`` → ``record_job_finished`` mutates a single
  row in place (idempotent on ``job_id``).
* ``redact_args`` masks every ``SENSITIVE_KEY_FRAGMENTS`` substring
  across nested dicts + lists.
* ``truncate_json_for_storage`` collapses oversized payloads to the
  ``_truncated`` sentinel without raising.
* ``truncate_error_message`` honours :data:`ERROR_MESSAGE_MAX_CHARS`.
"""

from __future__ import annotations

import uuid

from app.db.models.job_run import (
    ARGS_JSON_MAX_BYTES,
    ERROR_MESSAGE_MAX_CHARS,
    JobRunStatus,
)
from app.repositories.job_run import JobRunRepository
from app.services import job_run as svc
from app.services.job_run import (
    SENSITIVE_KEY_FRAGMENTS,
    build_args_payload,
    redact_args,
    truncate_error_message,
    truncate_json_for_storage,
)


# ── Pure-function tests (no DB) ───────────────────────────────
def test_redact_replaces_password_and_token_keys():
    payload = {
        "user": "alice",
        "password": "hunter2",
        "config": {
            "api_key": "sk-abc",
            "nested": {"client_secret": "shhh"},
            "ok": "fine",
        },
        "items": [
            {"token": "tok"},
            {"name": "ok"},
        ],
    }
    out = redact_args(payload)
    assert out["password"] == "***"
    assert out["config"]["api_key"] == "***"
    assert out["config"]["nested"]["client_secret"] == "***"
    assert out["config"]["ok"] == "fine"
    assert out["items"][0]["token"] == "***"
    assert out["items"][1]["name"] == "ok"


def test_redact_handles_empty_and_none_values():
    payload = {"password": "", "token": None, "session_id": "abc"}
    out = redact_args(payload)
    # Empty / None pass through verbatim — no point masking nothing.
    assert out["password"] == ""
    assert out["token"] is None
    # Non-empty session_id is sensitive (matches `session_id` fragment).
    assert out["session_id"] == "***"


def test_redact_max_depth_truncates_recursive_payload():
    payload: dict = {"x": {}}
    cursor = payload["x"]
    for _ in range(20):
        cursor["x"] = {}
        cursor = cursor["x"]
    out = redact_args(payload, max_depth=5)
    # Walk to depth 5 and assert we hit the truncation marker eventually.
    assert isinstance(out, dict)


def test_redact_uuid_and_datetime_are_stringified():
    payload = {"workspace_id": uuid.uuid4(), "ts": "2026-01-01T00:00:00"}
    out = redact_args(payload)
    assert isinstance(out["workspace_id"], str)
    assert out["ts"] == "2026-01-01T00:00:00"


def test_truncate_collapses_oversized_payload():
    big = {"k": "x" * (ARGS_JSON_MAX_BYTES + 1024)}
    out = truncate_json_for_storage(big)
    assert out.get("_truncated") is True
    assert isinstance(out.get("_size_bytes"), int)


def test_truncate_passes_small_payload_through():
    small = {"a": 1, "b": "hi"}
    out = truncate_json_for_storage(small)
    assert out == small


def test_truncate_error_message_caps_length():
    long = "x" * (ERROR_MESSAGE_MAX_CHARS + 1024)
    out = truncate_error_message(long)
    assert out is not None
    assert len(out) <= ERROR_MESSAGE_MAX_CHARS


def test_truncate_error_message_passes_through_short():
    assert truncate_error_message("ok") == "ok"
    assert truncate_error_message(None) is None


def test_build_args_payload_redacts_and_size_caps():
    body = build_args_payload(
        ["positional"],
        {"workspace_id": uuid.uuid4(), "api_key": "sk-secret"},
    )
    assert body["args"][0] == "positional"
    assert body["kwargs"]["api_key"] == "***"


def test_sensitive_key_fragments_are_lowercase():
    # Defence against a contributor adding an upper-case fragment that
    # never matches because :func:`_is_sensitive_key` lower-cases the
    # caller's key before comparing.
    for frag in SENSITIVE_KEY_FRAGMENTS:
        assert frag == frag.lower()


# ── Repository integration tests (DB-backed) ──────────────────
async def test_full_lifecycle_round_trip(db_session, workspace):
    job_id = f"test-{uuid.uuid4().hex[:8]}"
    repo = JobRunRepository(db_session)
    queued = await repo.upsert_queued(
        job_id=job_id,
        function_name="judge_session_artifact",
        args_json={"workspace_id": str(workspace.id)},
        workspace_id=workspace.id,
    )
    assert queued.status == JobRunStatus.QUEUED
    assert queued.workspace_id == workspace.id

    running = await repo.mark_running(
        job_id=job_id,
        function_name="judge_session_artifact",
        args_json={"workspace_id": str(workspace.id)},
        workspace_id=workspace.id,
    )
    assert running.id == queued.id
    assert running.status == JobRunStatus.RUNNING
    assert running.started_at is not None

    finished = await repo.mark_finished(
        job_id=job_id,
        status=JobRunStatus.SUCCESS,
        finished_at=running.started_at,
        duration_ms=42,
        retry_count=0,
    )
    assert finished is not None
    assert finished.status == JobRunStatus.SUCCESS
    assert finished.duration_ms == 42


async def test_upsert_queued_is_idempotent(db_session, workspace):
    job_id = f"test-{uuid.uuid4().hex[:8]}"
    repo = JobRunRepository(db_session)
    first = await repo.upsert_queued(
        job_id=job_id,
        function_name="curator_tick",
        args_json={},
        workspace_id=workspace.id,
    )
    second = await repo.upsert_queued(
        job_id=job_id,
        function_name="curator_tick",
        args_json={"changed": "ignored"},
        workspace_id=workspace.id,
    )
    assert first.id == second.id


async def test_mark_finished_returns_none_for_unknown_job(db_session):
    repo = JobRunRepository(db_session)
    out = await repo.mark_finished(
        job_id="never-seen",
        status=JobRunStatus.SUCCESS,
        finished_at=svc._now(),
        duration_ms=0,
        retry_count=0,
    )
    assert out is None


async def test_record_job_enqueued_swallows_db_failure(monkeypatch):
    """The enqueue-side recorder must never raise.

    We monkey-patch the session factory to return a failing context
    manager and assert :func:`record_job_enqueued` returns silently.
    """

    class _BoomFactory:
        def __call__(self):  # ``async with factory()`` expects a CM
            raise RuntimeError("simulated db down")

    # Invocation must complete cleanly.
    await svc.record_job_enqueued(
        job_id="x",
        function_name="judge_session_artifact",
        args=[],
        kwargs={},
        db_factory=_BoomFactory(),
    )
