"""Unit: M2.5.4 MEDIA wrapper — MCP tool result → RunEvent payload.

Pure conversions; no DB / SDK / network. Validates that:

* image / audio / file parts populate ``RunEvent.data['media']``.
* text-only results land in ``data['result']`` with an empty media
  list (so the frontend can rely on the key existing).
* unknown content kinds fall through into ``media`` with
  ``type='resource'`` rather than being dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.harness.mcp_runtime import (
    build_mcp_tool_result_event,
    media_part_to_wire,
    tool_result_to_event_data,
)
from app.agents.kernels.base import RunEventKind
from app.services.mcp_client import (
    McpMediaPart,
    McpToolResult,
    _wrap_tool_result,
)


# ─── Fake SDK part shapes ──────────────────────────────────────
@dataclass(slots=True)
class _SdkText:
    text: str
    type: str = "text"


@dataclass(slots=True)
class _SdkImage:
    data: str
    mimeType: str = "image/png"  # noqa: N815 - SDK shape
    type: str = "image"


@dataclass(slots=True)
class _SdkAudio:
    data: str
    mimeType: str = "audio/wav"  # noqa: N815 - SDK shape
    type: str = "audio"


@dataclass(slots=True)
class _SdkResource:
    type: str = "resource"
    resource: object | None = None


@dataclass(slots=True)
class _SdkResourceInner:
    uri: str
    mimeType: str | None = "application/pdf"  # noqa: N815 - SDK shape
    name: str | None = "doc.pdf"


@dataclass(slots=True)
class _SdkCallToolResult:
    content: list[object]
    isError: bool = False  # noqa: N815 - SDK shape


# ─── _wrap_tool_result ─────────────────────────────────────────
def test_wrap_tool_result_text_only():
    raw = _SdkCallToolResult(
        content=[_SdkText(text="hello "), _SdkText(text="world")]
    )
    wrapped = _wrap_tool_result(raw)
    assert wrapped.text == "hello world"
    assert wrapped.media == []
    assert not wrapped.is_error


def test_wrap_tool_result_image_and_audio():
    raw = _SdkCallToolResult(
        content=[
            _SdkText(text="here is the screenshot"),
            _SdkImage(data="iVBORw=="),
            _SdkAudio(data="UklGRg=="),
        ]
    )
    wrapped = _wrap_tool_result(raw)
    assert wrapped.text == "here is the screenshot"
    assert len(wrapped.media) == 2
    assert wrapped.media[0].kind == "image"
    assert wrapped.media[0].mime == "image/png"
    assert wrapped.media[0].data_b64 == "iVBORw=="
    assert wrapped.media[1].kind == "audio"
    assert wrapped.media[1].mime == "audio/wav"


def test_wrap_tool_result_resource_part_becomes_file():
    raw = _SdkCallToolResult(
        content=[
            _SdkResource(
                resource=_SdkResourceInner(
                    uri="https://files.example.com/x.pdf",
                    mimeType="application/pdf",
                    name="x.pdf",
                )
            )
        ]
    )
    wrapped = _wrap_tool_result(raw)
    assert wrapped.text == ""
    assert len(wrapped.media) == 1
    media = wrapped.media[0]
    assert media.kind == "file"
    assert media.url == "https://files.example.com/x.pdf"
    assert media.mime == "application/pdf"


def test_wrap_tool_result_unknown_part_kind_falls_through():
    @dataclass(slots=True)
    class _Mystery:
        type: str = "weird-future-kind"

    raw = _SdkCallToolResult(content=[_Mystery()])
    wrapped = _wrap_tool_result(raw)
    assert len(wrapped.media) == 1
    assert wrapped.media[0].kind == "resource"


def test_wrap_tool_result_is_error_propagates():
    raw = _SdkCallToolResult(
        content=[_SdkText(text="boom")],
        isError=True,
    )
    wrapped = _wrap_tool_result(raw)
    assert wrapped.is_error
    assert wrapped.text == "boom"


# ─── tool_result_to_event_data / build_mcp_tool_result_event ───
def test_event_data_includes_media_list_even_when_empty():
    result = McpToolResult(text="ok")
    data = tool_result_to_event_data(call_id="c1", tool_name="echo", result=result)
    assert data["id"] == "c1"
    assert data["name"] == "echo"
    assert data["result"] == "ok"
    assert data["media"] == []
    assert data["is_error"] is False


def test_event_data_renders_media_parts():
    result = McpToolResult(
        text="screenshot taken",
        media=[
            McpMediaPart(kind="image", mime="image/jpeg", data_b64="zzz"),
            McpMediaPart(kind="file", mime="application/pdf", url="https://x/y.pdf"),
        ],
    )
    data = tool_result_to_event_data(
        call_id="abc", tool_name="capture", result=result
    )
    assert data["media"][0] == {
        "type": "image",
        "mime": "image/jpeg",
        "data": "zzz",
    }
    assert data["media"][1] == {
        "type": "file",
        "mime": "application/pdf",
        "url": "https://x/y.pdf",
    }


def test_build_mcp_tool_result_event_kind_is_tool_result():
    result = McpToolResult(text="ok")
    event = build_mcp_tool_result_event(
        call_id="c", tool_name="t", result=result
    )
    assert event.kind == RunEventKind.TOOL_RESULT
    assert event.data["name"] == "t"
    assert event.data["result"] == "ok"


def test_media_part_to_wire_includes_filename_when_present():
    part = McpMediaPart(
        kind="file",
        mime="application/pdf",
        url="https://x/y.pdf",
        filename="report.pdf",
    )
    wire = media_part_to_wire(part)
    assert wire["filename"] == "report.pdf"


def test_media_part_to_wire_minimal_when_only_kind():
    part = McpMediaPart(kind="resource")
    wire = media_part_to_wire(part)
    assert wire == {"type": "resource"}
