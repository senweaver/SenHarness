"""Tests for stuck-loop detection (``ReliabilityState`` + ``StuckLoopAbort``).

Covers:
    * Repeated identical tool calls trigger ``is_stuck`` → True.
    * ``maybe_raise_stuck_loop`` raises ``StuckLoopAbort`` when the policy
      sets ``stuck_loop_abort=True`` (default), once per run.
    * Different argument hashes count as separate signatures so a benign
      varied loop does not trip the detector.
    * Disabling the feature short-circuits everything.
"""

from __future__ import annotations

import pytest

from app.agents.harness.reliability import (
    StuckLoopAbort,
    build_state,
)


def test_stuck_loop_raises_after_threshold() -> None:
    state = build_state(policy={"reliability": {}}, max_iterations=12)
    for _ in range(2):
        state.record_tool_call("read_file", {"path": "/tmp/a.txt"})
        # Below the default threshold of 3 — must not raise.
        state.maybe_raise_stuck_loop()

    state.record_tool_call("read_file", {"path": "/tmp/a.txt"})
    with pytest.raises(StuckLoopAbort) as excinfo:
        state.maybe_raise_stuck_loop()
    assert excinfo.value.tool_name == "read_file"
    assert excinfo.value.count == 3
    assert excinfo.value.threshold == 3


def test_stuck_loop_emits_only_once() -> None:
    state = build_state(policy={"reliability": {}}, max_iterations=12)
    for _ in range(5):
        state.record_tool_call("read_file", {"path": "/tmp/a.txt"})

    with pytest.raises(StuckLoopAbort):
        state.maybe_raise_stuck_loop()
    # Subsequent calls are no-ops — runner is responsible for unwinding now.
    state.maybe_raise_stuck_loop()
    state.maybe_raise_stuck_loop()


def test_varied_args_does_not_trip() -> None:
    """Same tool, different args ⇒ different signatures ⇒ no abort."""
    state = build_state(policy={"reliability": {}}, max_iterations=12)
    for i in range(5):
        state.record_tool_call("read_file", {"path": f"/tmp/file_{i}.txt"})
        state.maybe_raise_stuck_loop()


def test_abort_disabled_keeps_warning_only() -> None:
    """``stuck_loop_abort=False`` keeps the detector running but does not raise."""
    state = build_state(
        policy={"reliability": {"stuck_loop_abort": False}},
        max_iterations=12,
    )
    for _ in range(5):
        state.record_tool_call("read_file", {"path": "/tmp/a.txt"})
    state.maybe_raise_stuck_loop()
    stuck, repeated = state.is_stuck()
    assert stuck is True
    assert repeated == "read_file"


def test_detect_disabled_short_circuits() -> None:
    state = build_state(
        policy={"reliability": {"stuck_loop_detect": False}},
        max_iterations=12,
    )
    for _ in range(10):
        state.record_tool_call("read_file", {"path": "/tmp/a.txt"})
    stuck, repeated = state.is_stuck()
    assert stuck is False
    assert repeated == ""
    state.maybe_raise_stuck_loop()
