"""Tests for the DingTalk provider.

Covers both transports the provider supports today:

* Custom-robot webhook — HMAC signature lock-in (the original silent-
  bot pain point that v1 hardened against).
* Stream-mode OPEN-API outbound — the path that was completely broken
  before: ``post_reply`` used to require ``webhook_url`` even when the
  channel only stored ``client_id`` + ``client_secret``, so every
  stream-mode reply was silently dropped. These tests pin the new
  fork to ``oToMessages/batchSend`` (1:1) vs ``groupMessages/send``
  (group) so it can't regress to the dropped-on-the-floor state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx
import pytest

from app.services.channels.base import SignatureInvalid
from app.services.channels.dingtalk import (
    _OPENAPI_TOKEN_CACHE,
    DingTalkProvider,
    _compute_sign,
    build_thread_key,
)


def _valid_headers(secret: str) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    sign = _compute_sign(ts, secret)
    return {"timestamp": ts, "sign": sign}


class TestComputeSign:
    def test_matches_manual_hmac(self):
        """Lock in the exact DingTalk signing formula — any drift
        here means the bot's inbound requests start to fail."""
        ts = "1700000000000"
        secret = "SEC-test-secret"
        expected_payload = f"{ts}\n{secret}".encode()
        expected = base64.b64encode(
            hmac.new(
                secret.encode("utf-8"),
                expected_payload,
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        assert _compute_sign(ts, secret) == expected


class TestVerifySignature:
    def test_valid_signature_passes(self):
        provider = DingTalkProvider()
        secret = "SEC-test"
        provider.verify_signature(
            channel_config={"sign_secret": secret},
            headers=_valid_headers(secret),
            body=b"{}",
        )

    def test_bad_signature_rejected(self):
        provider = DingTalkProvider()
        headers = _valid_headers("SEC-real")
        headers["sign"] = "WRONGbase64=="
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": "SEC-real"},
                headers=headers,
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.signature_mismatch"

    def test_missing_headers_rejected(self):
        provider = DingTalkProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": "SEC"},
                headers={},
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.missing_signature_headers"

    def test_stale_timestamp_rejected(self):
        provider = DingTalkProvider()
        secret = "SEC"
        # 2 hours ago — well past the 60-second replay window.
        stale_ts = str(int((time.time() - 7200) * 1000))
        headers = {
            "timestamp": stale_ts,
            "sign": _compute_sign(stale_ts, secret),
        }
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": secret},
                headers=headers,
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.stale_timestamp"

    def test_bad_timestamp_format_rejected(self):
        provider = DingTalkProvider()
        with pytest.raises(SignatureInvalid) as exc:
            provider.verify_signature(
                channel_config={"sign_secret": "SEC"},
                headers={"timestamp": "not-a-number", "sign": "xxx"},
                body=b"{}",
            )
        assert exc.value.code == "dingtalk.bad_timestamp"

    def test_no_secret_skips_check(self):
        """Channels created before the V2 hardening may not have a
        ``sign_secret`` yet — for those we log a warning (elsewhere)
        and let the request through."""
        provider = DingTalkProvider()
        # Must not raise.
        provider.verify_signature(
            channel_config={},
            headers={},
            body=b"{}",
        )

    def test_verify_signatures_false_opts_out(self):
        """Operator explicitly disabling signature verification
        during dev-tunnel setup must not be blocked."""
        provider = DingTalkProvider()
        provider.verify_signature(
            channel_config={
                "sign_secret": "SEC",
                "verify_signatures": False,
            },
            headers={},
            body=b"{}",
        )


class TestParseInbound:
    def test_group_message_thread_key_includes_group_prefix(self):
        """Group chats must encode ``conversationType=='2'`` into a
        ``dingtalk:group:`` prefix so ``post_reply`` knows to call
        ``groupMessages/send`` (and not ``oToMessages/batchSend``,
        which would silently no-op for groups)."""
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {
                "msgtype": "text",
                "text": {"content": "  hello agent  "},
                "senderNick": "Alice",
                "senderStaffId": "alice-staff-id",
                "conversationId": "open-cid-1",
                "conversationType": "2",
            },
            headers={},
        )
        assert msg is not None
        assert msg.user_text == "hello agent"
        assert msg.external_user == "Alice"
        assert msg.thread_key == "dingtalk:group:open-cid-1"

    def test_private_message_thread_key_uses_staff_id(self):
        """1:1 chats need ``userIds=[senderStaffId]`` for outbound, so
        the thread_key has to carry the staff id, not the
        ``conversationId`` (which is opaque-per-user for private chats
        and useless to ``oToMessages/batchSend``)."""
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {
                "msgtype": "text",
                "text": {"content": "ping"},
                "senderNick": "Bob",
                "senderStaffId": "bob-staff-id",
                "conversationId": "private-cid-2",
                "conversationType": "1",
            },
            headers={},
        )
        assert msg is not None
        assert msg.thread_key == "dingtalk:user:bob-staff-id"

    def test_unknown_conversation_type_falls_back_to_cid(self):
        """Defensive: when conversationType is missing we still need a
        non-empty key so the dispatcher's session-bind doesn't choke,
        but we accept that ``post_reply`` will log + drop because the
        prefix is missing — the inbound side stays best-effort."""
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {
                "msgtype": "text",
                "text": {"content": "hi"},
                "conversationId": "weird-cid",
            },
            headers={},
        )
        assert msg is not None
        assert msg.thread_key == "weird-cid"

    def test_non_text_ignored(self):
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {"msgtype": "image", "image": {"url": "..."}},
            headers={},
        )
        assert msg is None

    def test_empty_text_ignored(self):
        provider = DingTalkProvider()
        msg = provider.parse_inbound(
            {"msgtype": "text", "text": {"content": "   "}},
            headers={},
        )
        assert msg is None


class TestBuildThreadKey:
    """Direct coverage for the helper because both the webhook
    ``parse_inbound`` and the stream handler call it; if it drifts the
    routing decision in :func:`_send_via_openapi` silently picks the
    wrong endpoint."""

    def test_group_uses_group_prefix(self):
        assert (
            build_thread_key(
                conversation_type="2",
                conversation_id="C-1",
                sender_staff_id="S-1",
            )
            == "dingtalk:group:C-1"
        )

    def test_private_uses_user_prefix(self):
        assert (
            build_thread_key(
                conversation_type="1",
                conversation_id="C-1",
                sender_staff_id="S-1",
            )
            == "dingtalk:user:S-1"
        )

    def test_missing_type_prefers_conversation_id(self):
        assert (
            build_thread_key(
                conversation_type=None,
                conversation_id="C-1",
                sender_staff_id="S-1",
            )
            == "C-1"
        )

    def test_all_empty_returns_constant_fallback(self):
        assert (
            build_thread_key(
                conversation_type=None,
                conversation_id=None,
                sender_staff_id=None,
            )
            == "dingtalk:fallback"
        )


# ─── Outbound tests ─────────────────────────────────────────
# We mock httpx.AsyncClient at the module level — the provider opens
# its own client per-call, so dependency-injection isn't an option
# without changing the public signature. ``monkeypatch`` on the class
# is the least-intrusive way to assert "the right URL + payload went
# out the door".


class _FakeResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        json_body: dict[str, Any] | None = None,
    ):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = json.dumps(self._json)
        self.content = self.text.encode()

    def json(self) -> dict[str, Any]:
        return self._json


class _RecordingClient:
    """Captures every POST so a single test can assert the full call
    sequence (token fetch, then send)."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})
        if not self._responses:
            raise AssertionError(f"unexpected extra POST to {url}")
        return self._responses.pop(0)


@pytest.fixture(autouse=True)
def _clear_openapi_token_cache():
    """The token cache is module-global; isolate every test."""
    _OPENAPI_TOKEN_CACHE.clear()
    yield
    _OPENAPI_TOKEN_CACHE.clear()


def _install_fake_client(monkeypatch: pytest.MonkeyPatch, client: _RecordingClient) -> None:
    """Patch :class:`httpx.AsyncClient` so the provider's ``with httpx.AsyncClient()`` block returns our recorder."""

    def factory(*args: Any, **kwargs: Any) -> _RecordingClient:
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)


class TestPostReplyStreamMode:
    """The bug this fixes: stream-mode channels store only
    ``client_id`` + ``client_secret`` (no ``webhook_url``), and the old
    ``post_reply`` early-returned in that case so every reply was
    dropped. These tests pin the new OPEN-API path."""

    async def test_group_calls_group_messages_send(self, monkeypatch):
        provider = DingTalkProvider()
        client = _RecordingClient(
            [
                _FakeResponse(json_body={"accessToken": "tok-1", "expireIn": 7200}),
                _FakeResponse(json_body={"errcode": 0}),
            ]
        )
        _install_fake_client(monkeypatch, client)

        await provider.post_reply(
            channel_config={
                "client_id": "ding_app_key",
                "client_secret": "ding_app_secret",
            },
            thread_key="dingtalk:group:open-cid-42",
            text="hello group",
        )

        assert len(client.calls) == 2

        token_call = client.calls[0]
        assert token_call["url"].endswith("/v1.0/oauth2/accessToken")
        assert token_call["json"] == {
            "appKey": "ding_app_key",
            "appSecret": "ding_app_secret",
        }

        send_call = client.calls[1]
        assert send_call["url"].endswith("/v1.0/robot/groupMessages/send")
        assert send_call["headers"].get("x-acs-dingtalk-access-token") == "tok-1"
        body = send_call["json"]
        assert body["robotCode"] == "ding_app_key"
        assert body["openConversationId"] == "open-cid-42"
        assert body["msgKey"] == "sampleMarkdown"
        assert json.loads(body["msgParam"])["text"] == "hello group"
        assert "userIds" not in body  # group endpoint must not get private fields

    async def test_private_calls_oto_batch_send(self, monkeypatch):
        provider = DingTalkProvider()
        client = _RecordingClient(
            [
                _FakeResponse(json_body={"accessToken": "tok-2", "expireIn": 7200}),
                _FakeResponse(json_body={"errcode": 0}),
            ]
        )
        _install_fake_client(monkeypatch, client)

        await provider.post_reply(
            channel_config={
                "client_id": "ding_app_key",
                "client_secret": "ding_app_secret",
            },
            thread_key="dingtalk:user:staff-7",
            text="hello user",
        )

        send_call = client.calls[1]
        assert send_call["url"].endswith("/v1.0/robot/oToMessages/batchSend")
        body = send_call["json"]
        assert body["robotCode"] == "ding_app_key"
        assert body["userIds"] == ["staff-7"]
        assert body["msgKey"] == "sampleMarkdown"
        assert "openConversationId" not in body

    async def test_access_token_cached_across_calls(self, monkeypatch):
        """Two replies in a row must hit ``oauth2/accessToken`` exactly
        once — DingTalk rate-limits that endpoint hard."""
        provider = DingTalkProvider()
        client = _RecordingClient(
            [
                _FakeResponse(json_body={"accessToken": "cached-tok", "expireIn": 7200}),
                _FakeResponse(json_body={"errcode": 0}),
                _FakeResponse(json_body={"errcode": 0}),
            ]
        )
        _install_fake_client(monkeypatch, client)

        for _ in range(2):
            await provider.post_reply(
                channel_config={
                    "client_id": "k",
                    "client_secret": "s",
                },
                thread_key="dingtalk:user:u",
                text="ping",
            )

        urls = [c["url"] for c in client.calls]
        assert urls.count("https://api.dingtalk.com/v1.0/oauth2/accessToken") == 1
        assert urls.count("https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend") == 2

    async def test_malformed_thread_key_skips_send(self, monkeypatch, caplog):
        """A thread_key without our prefix can't be routed — we log
        and drop rather than guess. Crucially we must NOT fetch a
        token for a request we can't even send."""
        provider = DingTalkProvider()
        client = _RecordingClient([])  # any HTTP call here is a bug
        _install_fake_client(monkeypatch, client)

        with caplog.at_level("WARNING"):
            await provider.post_reply(
                channel_config={
                    "client_id": "k",
                    "client_secret": "s",
                },
                thread_key="legacy-bare-cid",
                text="should not send",
            )

        assert client.calls == []
        assert any("malformed thread_key" in rec.message for rec in caplog.records)

    async def test_token_failure_drops_reply(self, monkeypatch, caplog):
        provider = DingTalkProvider()
        client = _RecordingClient(
            [_FakeResponse(status_code=401, json_body={"code": "InvalidAuthentication"})]
        )
        _install_fake_client(monkeypatch, client)

        with caplog.at_level("WARNING"):
            await provider.post_reply(
                channel_config={
                    "client_id": "k",
                    "client_secret": "wrong",
                },
                thread_key="dingtalk:user:u",
                text="hi",
            )

        # Only the token call happened — we never attempted to send.
        assert len(client.calls) == 1
        assert client.calls[0]["url"].endswith("/v1.0/oauth2/accessToken")


class TestPostReplyWebhookMode:
    """Webhook-mode (custom robot) replies still work the way they
    used to. We pin the signed-URL shape because the signature is the
    actual auth — DingTalk rejects unsigned posts with no informative
    error."""

    async def test_signs_url_with_timestamp_and_sign(self, monkeypatch):
        provider = DingTalkProvider()
        client = _RecordingClient([_FakeResponse(json_body={"errcode": 0})])
        _install_fake_client(monkeypatch, client)

        await provider.post_reply(
            channel_config={
                "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=AT",
                "sign_secret": "SEC",
            },
            thread_key="dingtalk:group:cid",
            text="hello",
        )

        assert len(client.calls) == 1
        url = client.calls[0]["url"]
        assert url.startswith("https://oapi.dingtalk.com/robot/send?access_token=AT&timestamp=")
        assert "&sign=" in url

    async def test_drops_when_neither_credential_set(self, monkeypatch, caplog):
        provider = DingTalkProvider()
        client = _RecordingClient([])
        _install_fake_client(monkeypatch, client)

        with caplog.at_level("WARNING"):
            await provider.post_reply(
                channel_config={},
                thread_key="dingtalk:user:u",
                text="hi",
            )

        assert client.calls == []
        assert any(
            "neither client_id/secret nor webhook_url" in rec.message for rec in caplog.records
        )
