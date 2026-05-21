"""Native typing indicator on the WeChat (iLink) channel.

The provider drives iLink's two-step typing protocol —
``POST /ilink/bot/getconfig`` for a ``typing_ticket`` (cached per bot
~24h), then a keepalive loop hitting ``POST /ilink/bot/sendtyping`` with
``status=1`` every few seconds. Cancellation shields a final
``status=2`` so the indicator clears the instant the agent's real reply
is ready.

These tests pin the contract end-to-end without hitting the live API:
ticket cache hits skip ``getconfig``, the keepalive sends the start
ping, cancel runs the stop ping in a shielded ``finally``, and the
provider falls back to a one-shot placeholder text when ``getconfig``
isn't available.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.channels import _wechat_ilink as ilink
from app.services.channels.wechat import WeChatProvider


@pytest.fixture(autouse=True)
def _clear_ticket_cache():
    ilink._TYPING_TICKET_CACHE.clear()
    yield
    ilink._TYPING_TICKET_CACHE.clear()


class _FakeResponse:
    def __init__(self, status_code: int = 200, body: dict[str, Any] | None = None):
        self.status_code = status_code
        self._body = body or {"ret": 0}
        self.content = b"{}"
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    def __init__(self, responses: dict[str, _FakeResponse]):
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        for path, resp in self._responses.items():
            if url.endswith(path):
                self.calls.append((path, json))
                return resp
        raise AssertionError(f"unexpected POST {url}")


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    def factory(*_a: Any, **_kw: Any) -> _FakeClient:
        return client

    monkeypatch.setattr(ilink.httpx, "AsyncClient", factory)


async def test_fetch_typing_ticket_caches_per_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {"/ilink/bot/getconfig": _FakeResponse(body={"ret": 0, "typing_ticket": "tkt-A"})}
    )
    _patch_client(monkeypatch, client)

    first = await ilink.fetch_typing_ticket(bot_token="bot-1", base_url=ilink._BASE)
    second = await ilink.fetch_typing_ticket(bot_token="bot-1", base_url=ilink._BASE)
    assert first == "tkt-A"
    assert second == "tkt-A"
    assert len(client.calls) == 1


async def test_fetch_typing_ticket_returns_none_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {"/ilink/bot/getconfig": _FakeResponse(body={"ret": 0, "typing_ticket": ""})}
    )
    _patch_client(monkeypatch, client)

    assert await ilink.fetch_typing_ticket(bot_token="bot-2", base_url=ilink._BASE) is None


async def test_fetch_typing_ticket_returns_none_on_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient({"/ilink/bot/getconfig": _FakeResponse(status_code=502)})
    _patch_client(monkeypatch, client)

    assert await ilink.fetch_typing_ticket(bot_token="bot-3", base_url=ilink._BASE) is None
    assert "bot-3" not in ilink._TYPING_TICKET_CACHE


async def test_send_typing_status_invalidates_ticket_on_session_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {"/ilink/bot/sendtyping": _FakeResponse(body={"ret": -14, "errmsg": "expired"})}
    )
    _patch_client(monkeypatch, client)
    ilink._TYPING_TICKET_CACHE["bot-4"] = ("stale", 1e18)

    with pytest.raises(RuntimeError, match="-14"):
        await ilink.send_typing_status(
            bot_token="bot-4",
            base_url=ilink._BASE,
            to_user_id="u1",
            typing_ticket="stale",
            status=1,
        )
    assert "bot-4" not in ilink._TYPING_TICKET_CACHE


async def test_typing_keepalive_sends_start_then_stop_on_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({"/ilink/bot/sendtyping": _FakeResponse(body={"ret": 0})})
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(ilink, "_TYPING_KEEPALIVE_SEC", 60.0)

    task = asyncio.create_task(
        ilink.run_typing_keepalive(
            bot_token="bot-5",
            base_url=ilink._BASE,
            to_user_id="u1",
            typing_ticket="tkt",
        )
    )
    await asyncio.sleep(0)
    for _ in range(20):
        if client.calls:
            break
        await asyncio.sleep(0)
    assert client.calls and client.calls[0][1]["status"] == 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any(call[1]["status"] == 2 for call in client.calls), client.calls


async def test_provider_send_processing_uses_native_typing_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient(
        {
            "/ilink/bot/getconfig": _FakeResponse(body={"ret": 0, "typing_ticket": "tkt-X"}),
            "/ilink/bot/sendtyping": _FakeResponse(body={"ret": 0}),
        }
    )
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(ilink, "_TYPING_KEEPALIVE_SEC", 60.0)

    sent_replies: list[str] = []

    async def fake_send_reply(**kwargs: Any) -> None:
        sent_replies.append(kwargs["text"])

    monkeypatch.setattr(
        "app.services.channels.wechat._wechat_ilink_send_reply", fake_send_reply, raising=False
    )

    provider = WeChatProvider()
    config = {"bot_token": "bot-6"}
    task = asyncio.create_task(
        provider.send_processing_indicator(
            channel_config=config,
            thread_key="wechat:user-1:ctx-1:user-1",
            text="💭 ignored",
        )
    )
    for _ in range(20):
        if any(c[0] == "/ilink/bot/sendtyping" for c in client.calls):
            break
        await asyncio.sleep(0)

    assert any(
        c[0] == "/ilink/bot/sendtyping" and c[1]["status"] == 1 for c in client.calls
    )
    assert not sent_replies, "native typing must not fall back to text placeholder"

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_provider_falls_back_to_text_when_getconfig_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient({"/ilink/bot/getconfig": _FakeResponse(status_code=403)})
    _patch_client(monkeypatch, client)

    sent_replies: list[dict[str, Any]] = []

    async def fake_send_reply(**kwargs: Any) -> None:
        sent_replies.append(kwargs)

    monkeypatch.setattr(ilink, "send_reply", fake_send_reply)

    provider = WeChatProvider()
    await provider.send_processing_indicator(
        channel_config={"bot_token": "bot-7"},
        thread_key="wechat:user-9:ctx-9:user-9",
        text="💭 fallback",
    )

    assert len(sent_replies) == 1
    assert sent_replies[0]["text"] == "💭 fallback"


async def test_provider_returns_silently_without_bot_token() -> None:
    provider = WeChatProvider()
    await provider.send_processing_indicator(
        channel_config={},
        thread_key="wechat:user:ctx:user",
        text="hi",
    )


async def test_provider_returns_silently_for_bad_thread_key() -> None:
    provider = WeChatProvider()
    await provider.send_processing_indicator(
        channel_config={"bot_token": "x"},
        thread_key="slack:foo",
        text="hi",
    )
