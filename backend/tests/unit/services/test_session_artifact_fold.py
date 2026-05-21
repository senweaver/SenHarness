"""Pure-function unit tests for ``_fold_events_to_turns`` (M0.2).

The fold has to be airtight because PRM / Curator / Evolver all read
its output. We exercise the recognised event vocabulary plus the
fail-quiet behaviour for unknown frame kinds.
"""

from __future__ import annotations

import uuid

from app.schemas.session_artifact import TurnRole
from app.services.session_artifact import (
    _fold_events_to_turns,
    _hash_user_text,
    _infer_final_outcome,
)


def test_empty_events_yields_only_user_turn():
    turns, tools, iters = _fold_events_to_turns([], "do the thing")
    assert len(turns) == 1
    assert turns[0].role == TurnRole.USER
    assert turns[0].text == "do the thing"
    assert turns[0].iteration == 0
    assert tools == []
    assert iters == 0


def test_single_assistant_delta_creates_one_assistant_turn():
    events = [
        {"kind": "delta", "data": {"text": "Hel"}},
        {"kind": "delta", "data": {"text": "lo"}},
        {"kind": "final", "data": {"message_id": str(uuid.uuid4())}},
    ]
    turns, tools, iters = _fold_events_to_turns(events, "hi")
    assert iters == 1
    assert len(turns) == 2
    assert turns[1].role == TurnRole.ASSISTANT
    assert turns[1].text == "Hello"
    assert turns[1].iteration == 1
    assert turns[1].message_id is not None
    assert tools == []


def test_tool_call_and_result_pair_attach_to_same_turn():
    call_id = "tc-1"
    events = [
        {"kind": "delta", "data": {"text": "Let me check… "}},
        {"kind": "tool_call", "data": {"id": call_id, "name": "search", "args": {"q": "x"}}},
        {"kind": "tool_result", "data": {"id": call_id, "result": ["a", "b"]}},
    ]
    turns, tools, iters = _fold_events_to_turns(events, "what's x?")
    assert iters == 1
    asst = turns[1]
    assert asst.role == TurnRole.ASSISTANT
    assert asst.text.startswith("Let me check")
    assert asst.tool_calls == [{"name": "search", "args": {"q": "x"}, "call_id": call_id}]
    assert asst.tool_results == [
        {"call_id": call_id, "ok": True, "data": ["a", "b"], "error": None}
    ]
    assert tools == ["search"]


def test_multiple_iterations_bump_counter():
    """delta → call → result → delta → call → result → delta(final)."""
    events = [
        {"kind": "delta", "data": {"text": "step1"}},
        {"kind": "tool_call", "data": {"id": "1", "name": "a", "args": {}}},
        {"kind": "tool_result", "data": {"id": "1", "result": "ok"}},
        {"kind": "delta", "data": {"text": "step2"}},
        {"kind": "tool_call", "data": {"id": "2", "name": "b", "args": {}}},
        {"kind": "tool_result", "data": {"id": "2", "result": "ok"}},
        {"kind": "delta", "data": {"text": "done"}},
        {"kind": "final", "data": {}},
    ]
    turns, tools, iters = _fold_events_to_turns(events, "go")
    assert iters == 3
    assistant_turns = [t for t in turns if t.role == TurnRole.ASSISTANT]
    assert len(assistant_turns) == 3
    assert [t.iteration for t in assistant_turns] == [1, 2, 3]
    assert tools == ["a", "b"]


def test_unknown_event_kinds_are_silently_dropped():
    events = [
        {"kind": "delta", "data": {"text": "ok"}},
        {"kind": "heartbeat", "data": {"ts": 123}},
        {"kind": "future_thing", "data": {}},
        {"not": "even-a-kind"},
        "garbage-string",
    ]
    turns, tools, iters = _fold_events_to_turns(events, "test")
    assert iters == 1
    assert tools == []
    assert turns[1].text == "ok"


def test_thinking_block_attaches_to_current_assistant():
    events = [
        {"kind": "thinking", "data": {"text": "internal "}},
        {"kind": "thinking", "data": {"text": "reasoning"}},
        {"kind": "delta", "data": {"text": "answer"}},
    ]
    turns, _tools, iters = _fold_events_to_turns(events, "q")
    assert iters == 1
    asst = turns[1]
    assert asst.thinking == "internal reasoning"
    assert asst.text == "answer"


def test_iteration_marker_explicit_bump():
    events = [
        {"kind": "delta", "data": {"text": "first"}},
        {"kind": "iteration_marker", "data": {}},
        {"kind": "delta", "data": {"text": "second"}},
    ]
    turns, _tools, iters = _fold_events_to_turns(events, "")
    assert iters == 2
    assistant_turns = [t for t in turns if t.role == TurnRole.ASSISTANT]
    assert [t.text for t in assistant_turns] == ["first", "second"]


def test_tool_result_with_error_marks_ok_false():
    events = [
        {"kind": "tool_call", "data": {"id": "x", "name": "boom", "args": {}}},
        {"kind": "tool_result", "data": {"id": "x", "error": "permission_denied"}},
    ]
    turns, tools, iters = _fold_events_to_turns(events, "q")
    asst = turns[1]
    assert asst.tool_results[0]["ok"] is False
    assert asst.tool_results[0]["error"] == "permission_denied"
    assert tools == ["boom"]
    assert iters == 1


def test_invoked_tools_are_sorted_unique():
    events = [
        {"kind": "tool_call", "data": {"id": "1", "name": "zeta"}},
        {"kind": "tool_call", "data": {"id": "2", "name": "alpha"}},
        {"kind": "tool_call", "data": {"id": "3", "name": "alpha"}},
    ]
    _turns, tools, _iters = _fold_events_to_turns(events, "")
    assert tools == ["alpha", "zeta"]


def test_hash_user_text_is_stable_across_normalisation():
    a = _hash_user_text("café")
    b = _hash_user_text("  café  ")
    assert a == b
    assert len(a) == 64
    # Different content → different hash.
    assert _hash_user_text("café") != _hash_user_text("Café")


def test_infer_outcome_success_on_clean_final():
    events = [
        {"kind": "delta", "data": {"text": "ok"}},
        {"kind": "final", "data": {}},
    ]
    outcome, kind = _infer_final_outcome(events, raised_exc=None)
    assert outcome == "success"
    assert kind is None


def test_infer_outcome_cancelled():
    import asyncio

    outcome, kind = _infer_final_outcome([], raised_exc=asyncio.CancelledError())
    assert outcome == "cancelled"
    assert kind is None


def test_infer_outcome_error_on_pure_failure():
    outcome, kind = _infer_final_outcome([], raised_exc=RuntimeError("boom"))
    assert outcome == "error"
    assert kind == "RuntimeError"


def test_infer_outcome_partial_when_some_output_then_exception():
    events = [{"kind": "delta", "data": {"text": "half"}}]
    outcome, kind = _infer_final_outcome(events, raised_exc=RuntimeError("mid"))
    assert outcome == "partial"
    assert kind == "RuntimeError"


def test_infer_outcome_partial_when_error_frame_with_output():
    events = [
        {"kind": "delta", "data": {"text": "got something"}},
        {"kind": "error", "data": {"code": "stuck_loop"}},
        {"kind": "final", "data": {}},
    ]
    outcome, kind = _infer_final_outcome(events, raised_exc=None)
    assert outcome == "partial"
    assert kind == "stuck_loop"


def test_message_id_overwrite_keeps_lineage():
    """A re-pointed FINAL frame (e.g. WS layer setting the persisted id)
    must win over any synthetic id the runner emitted."""
    msg_id = uuid.uuid4()
    events = [
        {"kind": "delta", "data": {"text": "answer"}},
        {"kind": "final", "data": {"message_id": str(msg_id)}},
    ]
    turns, _tools, _iters = _fold_events_to_turns(events, "q")
    assert turns[1].message_id == msg_id
