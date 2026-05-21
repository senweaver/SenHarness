"""Feishu / Lark provider (minimal ingest; outbound reply via open-apis v1).

Supports:
    * URL verification ``challenge`` echo.
    * ``event_callback`` with ``im.message.receive_v1`` events.
    * Request verification via the ``verification_token`` stored in
      ``config_json.verification_token`` (legacy flat-token mode) — matches
      either the top-level ``token`` field (1.0 schema) or
      ``header.token`` (2.0 schema).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.services.channels.base import (
    ChannelProvider,
    InboundDispatch,
    InboundMessage,
    SignatureInvalid,
)

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)

_FEISHU_BASE = "https://open.feishu.cn"

# tenant_access_token cache. The Feishu auth endpoint returns the token's
# ``expire`` window (seconds, normally 7200). Without this cache every
# outbound reply pays a full HTTPS round-trip just to fetch a token that
# is still valid — ~200-500ms wasted per message. WeCom and DingTalk
# already cache theirs; Feishu/Lark were the odd ones out.
_TENANT_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TENANT_TOKEN_REFRESH_EARLY_SEC = 60


async def _fetch_tenant_access_token(
    *,
    app_id: str,
    app_secret: str,
    client: httpx.AsyncClient,
) -> str | None:
    key = (app_id, app_secret)
    now = time.time()
    cached = _TENANT_TOKEN_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]
    try:
        resp = await client.post(
            f"{_FEISHU_BASE}/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
    except httpx.HTTPError as e:  # pragma: no cover - network path
        log.warning("feishu tenant_access_token network error: %s", e)
        return None
    body = resp.json() if resp.content else {}
    token = body.get("tenant_access_token")
    if not token:
        log.warning("feishu tenant_access_token fetch failed: %s", body)
        return None
    expire = int(body.get("expire") or 7200)
    _TENANT_TOKEN_CACHE[key] = (
        token,
        now + max(60, expire - _TENANT_TOKEN_REFRESH_EARLY_SEC),
    )
    return token


class FeishuProvider(ChannelProvider):
    kind = "feishu"

    @classmethod
    def metadata(cls):
        from app.services.channels.base import ChannelProviderMeta

        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Feishu / Lark",
            description=(
                "Feishu (or Lark) custom app. Verifies the "
                "verification_token on every push and replies via the "
                "Lark Open API using the app's tenant access token. "
                "Stream mode (lark-oapi WebSocket) lets it run without "
                "a public callback URL."
            ),
            docs_url="https://open.feishu.cn/document/",
            required_config_fields=(
                "app_id",
                "app_secret",
                "verification_token",
            ),
            optional_config_fields=("encrypt_key", "verify_signatures"),
            supports_outbound=True,
            supported_modes=("webhook", "stream"),
            default_mode="stream",
            stream_requires_extra="channels-stream",
            # Stream mode dials out via lark-oapi WebSocket, so we don't
            # need ``verification_token`` (that gates webhook auth) —
            # operators can finish setup with just the app credentials.
            mode_required_fields={
                "stream": ("app_id", "app_secret"),
                "webhook": ("app_id", "app_secret", "verification_token"),
            },
            mode_optional_fields={
                "stream": (),
                "webhook": ("encrypt_key",),
            },
        )

    @classmethod
    def supports_stream(cls) -> bool:
        return True

    @classmethod
    def stream_available(cls) -> bool:
        try:
            import lark_oapi  # noqa: F401
        except ImportError:
            return False
        return True

    async def run_stream(
        self,
        *,
        channel: Channel,
        dispatch: InboundDispatch,
        stop: asyncio.Event,
    ) -> None:
        from app.services.channels._lark_stream import run_oapi_ws_stream

        await run_oapi_ws_stream(
            channel=channel,
            dispatch=dispatch,
            stop=stop,
            domain="feishu",
        )

    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        if channel_config.get("verify_signatures") is False:
            return
        expected = channel_config.get("verification_token")
        if not expected:
            return  # not configured → skip for back-compat
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError as e:
            raise SignatureInvalid("feishu.bad_json", "malformed body") from e
        # Event 1.0 ships ``token`` at top level; 2.0 moves it into ``header``.
        got = payload.get("token") or (payload.get("header") or {}).get("token")
        if not got or got != expected:
            raise SignatureInvalid(
                "feishu.bad_token", "Feishu verification_token mismatch"
            )

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        if payload.get("type") == "url_verification":
            return None

        header = payload.get("header") or {}
        if header.get("event_type") != "im.message.receive_v1":
            return None
        event = payload.get("event") or {}
        msg = event.get("message") or {}
        sender = event.get("sender") or {}

        content_raw = msg.get("content") or "{}"
        try:
            content = json.loads(content_raw) if isinstance(content_raw, str) else content_raw
        except json.JSONDecodeError:
            content = {}

        text = (content.get("text") or "").strip()
        if not text:
            return None

        chat_id = msg.get("chat_id") or "unknown"
        root_id = msg.get("root_id") or msg.get("message_id") or ""
        sender_id = (sender.get("sender_id") or {}).get("open_id") or "feishu_user"

        return InboundMessage(
            thread_key=f"feishu:{chat_id}:{root_id}",
            user_text=text,
            external_user=sender_id,
            raw={
                "chat_id": chat_id,
                "message_id": msg.get("message_id"),
                "root_id": root_id,
                "event_id": header.get("event_id"),
            },
        )

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return None

    async def post_reply(
        self, *, channel_config: dict[str, Any], thread_key: str, text: str
    ) -> None:
        app_id = channel_config.get("app_id")
        app_secret = channel_config.get("app_secret")
        if not (app_id and app_secret):
            log.warning("feishu channel missing app_id/app_secret; skipping reply")
            return
        try:
            _, chat_id, _root_id = thread_key.split(":", 2)
        except ValueError:
            log.warning("malformed feishu thread_key %r", thread_key)
            return

        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                token = await _fetch_tenant_access_token(
                    app_id=app_id, app_secret=app_secret, client=c,
                )
                if not token:
                    return

                body = {
                    "receive_id": chat_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text[:4000]}),
                }
                r = await c.post(
                    f"{_FEISHU_BASE}/open-apis/im/v1/messages?receive_id_type=chat_id",
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = r.json() if r.content else {}
                if data.get("code") != 0:
                    log.warning("feishu send failed: %s", data)
        except Exception as e:  # pragma: no cover
            log.warning("feishu reply error: %s", e)
