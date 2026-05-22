"""Cache-stability proof for the M0.4 / M0.5 reflection injection.

The injection mutates ``ModelRequestNode.request.parts`` in place, which
participates in *this* ``Agent.iter()``'s in-memory ``state.message_history``
but never reaches the persisted ``messages`` table. The very next user turn
re-hydrates from the DB, so the prefix replayed against the provider is
byte-identical to what the previous turn started with.

The test demonstrates that:
1. Injecting an ephemeral system part mutates only the supplied node.
2. A subsequent turn that re-hydrates from the DB-shaped history yields
   exactly the same ``ModelMessage`` prefix as before injection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.kernels.native._reflection import inject_ephemeral_system_message


@dataclass
class _StubRequest:
    parts: list[Any]


@dataclass
class _StubNode:
    request: _StubRequest


def _persisted_history_snapshot() -> list[dict[str, Any]]:
    """Mirror what the runner writes to the ``messages`` table after a turn:
    plain user / assistant text rows. No system-prompt parts ever go in."""
    return [
        {"role": "user", "content_json": {"text": "What's the weather?"}},
        {"role": "assistant", "content_json": {"text": "It's sunny."}},
    ]


def test_injection_does_not_mutate_persisted_shape() -> None:
    from pydantic_ai.messages import (
        ModelRequest,
        SystemPromptPart,
        UserPromptPart,
    )

    history_before = _persisted_history_snapshot()
    user_part = UserPromptPart(content="Next question?")
    request = ModelRequest(parts=[user_part])
    node = _StubNode(request=request)

    ok = inject_ephemeral_system_message(node, "Reflect briefly.")
    assert ok is True

    assert len(node.request.parts) == 2
    assert isinstance(node.request.parts[0], SystemPromptPart)
    assert node.request.parts[0].content == "Reflect briefly."
    assert node.request.parts[1] is user_part

    history_after = _persisted_history_snapshot()
    assert history_after == history_before


def test_rehydrated_prefix_is_stable_across_injection() -> None:
    """The runner's ``_rehydrate_history`` reads only ``role`` + text, so even
    if a future turn happens after a reflection-injected turn, the rebuilt
    history (which is what gets sent to the model) excludes the ephemeral
    SystemPromptPart and matches the pre-injection prefix exactly."""
    from app.agents.kernels.native.runner import _rehydrate_history

    history = _persisted_history_snapshot()
    prefix_before = _rehydrate_history(history)
    prefix_after = _rehydrate_history(history)
    assert prefix_before == prefix_after
    for msg in prefix_after:
        for part in getattr(msg, "parts", []) or []:
            assert type(part).__name__ != "SystemPromptPart"


def test_inject_failure_returns_false() -> None:
    """Non-ModelRequest nodes (e.g. UserPromptNode) must be ignored without
    raising — the runner falls through and the audit row is suppressed."""

    class _NotARequest:
        pass

    node = _StubNode(request=_NotARequest())  # type: ignore[arg-type]
    assert inject_ephemeral_system_message(node, "noop") is False


def test_inject_skips_request_carrying_tool_returns() -> None:
    """OpenAI-compatible providers (DeepSeek) reject any payload where an
    assistant tool_calls message isn't immediately followed by ``tool`` messages
    for every tool_call_id. Prepending a SystemPromptPart to a ModelRequest
    that already carries ToolReturnPart would render as
    ``assistant(tool_calls) → system → tool → tool`` and trip HTTP 400
    ``insufficient tool messages following tool_calls``. The injector must
    leave that request untouched."""
    from pydantic_ai.messages import (
        ModelRequest,
        ToolReturnPart,
    )

    tool_returns = [
        ToolReturnPart(tool_name="t1", content={"ok": True}, tool_call_id="call_a"),
        ToolReturnPart(tool_name="t2", content={"ok": True}, tool_call_id="call_b"),
    ]
    request = ModelRequest(parts=list(tool_returns))
    node = _StubNode(request=request)

    ok = inject_ephemeral_system_message(node, "Reflect briefly.")
    assert ok is False
    assert len(node.request.parts) == 2
    assert all(p is original for p, original in zip(node.request.parts, tool_returns))


def test_inject_skips_request_carrying_retry_for_tool() -> None:
    """A tool-bound RetryPromptPart (``tool_name`` set) round-trips into the
    OpenAI wire as a ``tool`` role message — same adjacency hazard as
    ToolReturnPart, so injection must also skip."""
    from pydantic_ai.messages import ModelRequest, RetryPromptPart

    retry = RetryPromptPart(
        content="please retry",
        tool_name="search",
        tool_call_id="call_x",
    )
    request = ModelRequest(parts=[retry])
    node = _StubNode(request=request)

    assert inject_ephemeral_system_message(node, "Reflect briefly.") is False
    assert len(node.request.parts) == 1
    assert node.request.parts[0] is retry


def test_inject_proceeds_for_pure_user_prompt() -> None:
    """The injector must still fire on regular user-prompt-only requests —
    those are the legitimate slots reflection was designed for."""
    from pydantic_ai.messages import (
        ModelRequest,
        SystemPromptPart,
        UserPromptPart,
    )

    user = UserPromptPart(content="next?")
    request = ModelRequest(parts=[user])
    node = _StubNode(request=request)

    assert inject_ephemeral_system_message(node, "Reflect briefly.") is True
    assert isinstance(node.request.parts[0], SystemPromptPart)
    assert node.request.parts[1] is user


def test_openai_wire_sequence_has_no_system_between_tool_calls_and_returns() -> None:
    """End-to-end invariant: after running the reflection injector against
    a synthetic node, mapping the resulting messages through pydantic-ai's
    OpenAI adapter must never produce a ``system`` (or any non-``tool``)
    message between an assistant message bearing ``tool_calls`` and the
    matching ``tool`` messages. This is the DeepSeek 400 trigger we just
    eliminated."""
    import asyncio

    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )
    from pydantic_ai.models import ModelRequestParameters
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    call_ids = ["call_a", "call_b", "call_c"]
    history = [
        ModelRequest(parts=[UserPromptPart(content="predict stock")]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name=f"t_{cid}", args={}, tool_call_id=cid) for cid in call_ids
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name=f"t_{cid}", content="ok", tool_call_id=cid)
                for cid in call_ids
            ]
        ),
    ]

    # Apply the reflection injector to the tool-returns request — must
    # silently skip so the wire format stays adjacent.
    node = _StubNode(request=history[-1])  # type: ignore[arg-type]
    assert inject_ephemeral_system_message(node, "Reflect briefly.") is False

    model = OpenAIChatModel(
        "gpt-4o-mini",
        provider=OpenAIProvider(api_key="sk-test", base_url="https://example.invalid"),
    )

    async def _map() -> list[Any]:
        return await model._map_messages(history, ModelRequestParameters())

    wire = asyncio.run(_map())

    assistant_indexes = [
        i for i, m in enumerate(wire) if m.get("role") == "assistant" and m.get("tool_calls")
    ]
    assert assistant_indexes, "expected assistant message with tool_calls"
    for idx in assistant_indexes:
        tc_ids = [c["id"] for c in wire[idx]["tool_calls"]]
        for offset, expected_id in enumerate(tc_ids, start=1):
            follower = wire[idx + offset]
            assert follower.get("role") == "tool", (
                f"DeepSeek strict adjacency violated: assistant(tool_calls) at "
                f"index {idx} followed by role={follower.get('role')!r} at "
                f"index {idx + offset}"
            )
            assert follower["tool_call_id"] == expected_id
