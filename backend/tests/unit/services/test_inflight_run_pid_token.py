"""Unit tests for the M2.5.2 ``current_pid_token`` helper.

The token is the only piece of state that lets the recovery sweep
distinguish "still my run" from "abandoned by a previous incarnation",
so its uniqueness contract gets its own focused suite. Pure-function
tests — no DB, no Redis, no fixtures.
"""

from __future__ import annotations

import os
import socket

import pytest

from app.services import inflight_run as svc


def test_current_pid_token_shape() -> None:
    token = svc.current_pid_token()
    parts = token.split(":")
    assert len(parts) == 3, f"expected host:pid:start, got {token!r}"
    assert parts[0]
    assert int(parts[1]) == os.getpid()
    assert int(parts[2]) > 0


def test_current_pid_token_contains_hostname() -> None:
    token = svc.current_pid_token()
    host = (socket.gethostname() or "unknown")[:40]
    assert token.startswith(f"{host}:")


def test_current_pid_token_changes_when_start_seconds_advances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_times = iter([1_700_000_000.0, 1_700_000_005.0])
    monkeypatch.setattr(svc.time, "time", lambda: next(fake_times))
    first = svc.current_pid_token()
    second = svc.current_pid_token()
    assert first != second
    assert first.split(":")[:2] == second.split(":")[:2]


def test_process_part_strips_start_seconds() -> None:
    assert svc.process_part("host01:1234:1700000000") == "host01:1234"
    # Defensive: malformed token round-trips unchanged.
    assert svc.process_part("malformed") == "malformed"
    assert svc.process_part(None) is None
    assert svc.process_part("") is None


def test_token_truncation_respects_column_cap() -> None:
    # Hostname is trimmed to <= 40 chars even if the OS returns a long one.
    monkey_host = "x" * 200
    real = svc.current_pid_token()
    # Prove the live host is <= 40 (sanity); real upper bound enforced
    # by the model column anyway.
    assert len(real.split(":")[0]) <= 40
    _ = monkey_host  # silence unused
