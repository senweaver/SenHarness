"""Pairing repair for tool_call / tool_return parts in a pydantic-ai history.

The repair is the last stage of ``build_history_processors`` so every
payload that reaches an OpenAI-compatible upstream is guaranteed to obey
the assistant-tool-call ↔ tool-message pairing rule that DeepSeek (and
the spec) enforce. Tests cover the two failure modes that used to leak
through:

* ToolCallPart left behind after the matching ToolReturnPart was trimmed
  by a sliding-window stage above.
* ToolReturnPart left behind after the ToolCallPart was trimmed (the
  reverse, equally illegal under the OpenAI contract).
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from app.agents.harness.context import build_history_processors
from app.agents.harness.reliability import repair_orphan_tool_calls


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant_with_call(tid: str, name: str = "web_search") -> ModelResponse:
    return ModelResponse(
        parts=[
            TextPart(content="working on it"),
            ToolCallPart(tool_name=name, args={"q": "byd"}, tool_call_id=tid),
        ]
    )


def _tool_return(tid: str, payload: str = "ok") -> ModelRequest:
    return ModelRequest(
        parts=[ToolReturnPart(tool_name="web_search", content=payload, tool_call_id=tid)]
    )


def test_clean_history_passes_through() -> None:
    history = [
        _user("hi"),
        _assistant_with_call("c1"),
        _tool_return("c1"),
        ModelResponse(parts=[TextPart(content="done")]),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 4
    assert isinstance(repaired[1], ModelResponse)
    assert isinstance(repaired[1].parts[1], ToolCallPart)


def test_drops_tool_call_without_return() -> None:
    history = [
        _user("hi"),
        _assistant_with_call("c1"),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 2
    assert all(not isinstance(p, ToolCallPart) for p in repaired[1].parts)
    assert isinstance(repaired[1].parts[0], TextPart)


def test_drops_assistant_message_when_only_orphan_call() -> None:
    history = [
        _user("hi"),
        ModelResponse(parts=[ToolCallPart(tool_name="x", args={}, tool_call_id="c1")]),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 1
    assert isinstance(repaired[0], ModelRequest)


def test_drops_tool_return_without_call() -> None:
    history = [
        _user("hi"),
        _tool_return("c-stale"),
        ModelResponse(parts=[TextPart(content="hello")]),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 2
    assert isinstance(repaired[0], ModelRequest)
    assert isinstance(repaired[0].parts[0], UserPromptPart)
    assert isinstance(repaired[1], ModelResponse)


def test_mixed_orphans_in_one_request_keeps_paired_return() -> None:
    history = [
        _user("hi"),
        _assistant_with_call("c1"),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="web_search", content="ok", tool_call_id="c1"),
                ToolReturnPart(tool_name="web_search", content="ok", tool_call_id="c-stale"),
            ]
        ),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 3
    tail = repaired[-1]
    assert isinstance(tail, ModelRequest)
    assert len(tail.parts) == 1
    assert tail.parts[0].tool_call_id == "c1"


def test_partial_parallel_tool_calls_strip_unmatched() -> None:
    history = [
        _user("predict byd stock"),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="web_search", args={"q": "byd"}, tool_call_id="a"),
                ToolCallPart(tool_name="web_search", args={"q": "ev market"}, tool_call_id="b"),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="web_search", content="...", tool_call_id="a"),
            ]
        ),
    ]
    repaired = repair_orphan_tool_calls(history)
    assistant = repaired[1]
    assert isinstance(assistant, ModelResponse)
    assert len(assistant.parts) == 1
    assert assistant.parts[0].tool_call_id == "a"


def test_repair_is_idempotent() -> None:
    history = [
        _user("hi"),
        _assistant_with_call("c1"),
    ]
    once = repair_orphan_tool_calls(history)
    twice = repair_orphan_tool_calls(once)
    assert [type(m) for m in once] == [type(m) for m in twice]


def test_build_history_processors_chain_ends_with_repair() -> None:
    processors = build_history_processors(policy=None, primary_model=None)
    assert processors
    assert processors[-1] is repair_orphan_tool_calls


def test_drops_assistant_message_with_only_empty_textpart() -> None:
    """Thinking-mode models can finish a turn with an opened-but-empty
    TextPart (zero deltas streamed). Serialised on the OpenAI wire that
    becomes ``{"role": "assistant", "content": ""}`` and DeepSeek replies
    HTTP 400 ``Invalid assistant message: content or tool_calls must be
    set``. The repair pass must drop the whole message so the next iter
    request stays well-formed.
    """
    history = [
        _user("hi"),
        ModelResponse(parts=[TextPart(content="")]),
        _user("再问一次"),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 2
    assert all(isinstance(m, ModelRequest) for m in repaired)


def test_drops_whitespace_only_textpart() -> None:
    history = [
        _user("hi"),
        ModelResponse(parts=[TextPart(content="   \n\t ")]),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 1
    assert isinstance(repaired[0], ModelRequest)


def test_strips_empty_textpart_but_keeps_other_parts() -> None:
    history = [
        _user("hi"),
        ModelResponse(
            parts=[
                TextPart(content=""),
                ToolCallPart(tool_name="x", args={}, tool_call_id="c1"),
            ]
        ),
        _tool_return("c1"),
    ]
    repaired = repair_orphan_tool_calls(history)
    assert len(repaired) == 3
    assistant = repaired[1]
    assert isinstance(assistant, ModelResponse)
    assert len(assistant.parts) == 1
    assert isinstance(assistant.parts[0], ToolCallPart)
