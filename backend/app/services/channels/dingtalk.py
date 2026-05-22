"""DingTalk (钉钉) custom-bot inbound/outbound provider.

DingTalk's "custom robot" webhook posts messages as JSON to a URL
that includes a ``timestamp`` + ``sign`` query-string. We verify the
HMAC-SHA256 signature on every push (when ``sign_secret`` is set in
config) and reject anything older than a 60-second window to block
replay. Outbound replies go to the same webhook URL (``webhook_url``
in config) — DingTalk doesn't maintain server-side thread state for
custom robots, so ``thread_key`` is effectively "this channel".

Reference:
  * https://open.dingtalk.com/document/robots/customize-robot-security-settings
  * https://open.dingtalk.com/document/robots/receive-message

Config shape (config_json):

    webhook_url      (required)  — DingTalk custom-robot webhook URL,
                                   usually includes ``access_token``
                                   as a query param.
    sign_secret      (required)  — the "SEC..." signing secret shown
                                   next to the webhook in the bot
                                   admin UI.
    verify_signatures (optional, default True) — set False during
                                   dev tunnel setup when the proxy
                                   rewrites query strings.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

import httpx

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundDispatch,
    InboundMessage,
    SignatureInvalid,
)

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)

DINGTALK_TIMESTAMP_TOLERANCE_MS = 60 * 1000  # 60 seconds
DINGTALK_OPENAPI_BASE = "https://api.dingtalk.com"

# DingTalk's ``conversationType`` field uses string codes:
#   "1" → 1:1 private chat
#   "2" → group chat
_DINGTALK_GROUP_TYPE = "2"
_DINGTALK_PRIVATE_TYPE = "1"

# Outbound thread_key shape — picked so ``post_reply`` can pick which
# OPEN-API endpoint to call without re-fetching state. The dispatcher
# treats the key as opaque (just a stable id for "this conversation");
# we own its layout end-to-end.
_THREAD_KEY_PREFIX_GROUP = "dingtalk:group:"
_THREAD_KEY_PREFIX_USER = "dingtalk:user:"
_THREAD_KEY_FALLBACK = "dingtalk:fallback"


def build_thread_key(
    *,
    conversation_type: Any,
    conversation_id: Any,
    sender_staff_id: Any,
) -> str:
    """Encode the inbound's chat type into a stable, parseable key.

    Used by both the stream handler and the webhook ``parse_inbound``
    so :meth:`DingTalkProvider.post_reply` can route the outbound to
    the correct OPEN-API endpoint (``groupMessages/send`` vs
    ``oToMessages/batchSend``) without an extra DB lookup.
    """
    ctype = str(conversation_type or "").strip()
    cid = str(conversation_id or "").strip()
    staff = str(sender_staff_id or "").strip()
    if ctype == _DINGTALK_GROUP_TYPE and cid:
        return f"{_THREAD_KEY_PREFIX_GROUP}{cid}"
    if ctype == _DINGTALK_PRIVATE_TYPE and staff:
        return f"{_THREAD_KEY_PREFIX_USER}{staff}"
    # Defensive fallback: prefer conversation_id (gives session
    # continuity for group-ish unknowns) then sender_staff_id, finally
    # a constant so we never pass an empty key to the dispatcher.
    return cid or staff or _THREAD_KEY_FALLBACK


class DingTalkProvider(ChannelProvider):
    kind = "dingtalk"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="DingTalk (钉钉)",
            description=(
                "DingTalk custom-robot webhook OR Stream Mode (the "
                "official no-public-IP transport that uses ClientID + "
                "ClientSecret instead of webhook_url + sign_secret). "
                "Webhook path verifies HMAC-SHA256 with a 60s replay "
                "window; stream path uses dingtalk-stream's SDK."
            ),
            docs_url="https://open.dingtalk.com/document/robots/",
            required_config_fields=("webhook_url", "sign_secret"),
            optional_config_fields=(
                "verify_signatures",
                "client_id",
                "client_secret",
            ),
            supports_outbound=True,
            supported_modes=("webhook", "stream"),
            default_mode="stream",
            stream_requires_extra="channels-stream",
            # Stream and webhook are essentially two unrelated transports
            # for DingTalk: one dials Tencent's stream gateway with the
            # app's ClientID/Secret, the other receives signed pushes at
            # ``webhook_url``. Splitting required fields per mode keeps
            # the form from showing "webhook_url" on a stream channel.
            mode_required_fields={
                "stream": ("client_id", "client_secret"),
                "webhook": ("webhook_url", "sign_secret"),
            },
            # Without an explicit per-mode optional list the form would
            # fall back to the global ``optional_config_fields`` and
            # render ``client_id`` / ``client_secret`` a second time
            # under stream mode (they're already required there) — and
            # ``verify_signatures`` would leak into stream mode where it
            # has no meaning. Pin the optional set per mode instead:
            #   - stream: nothing optional (the OPEN-API credential pair
            #     is already required, no toggles apply).
            #   - webhook: ``verify_signatures`` is the only knob, and
            #     even that one is filtered out of the visible form by
            #     the frontend (it's an advanced dev-tunnel escape hatch).
            mode_optional_fields={
                "stream": (),
                "webhook": ("verify_signatures",),
            },
        )

    @classmethod
    def supports_stream(cls) -> bool:
        return True

    @classmethod
    def stream_available(cls) -> bool:
        try:
            import dingtalk_stream  # noqa: F401
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
        from app.services.channels._dingtalk_stream import run_stream_client

        await run_stream_client(channel=channel, dispatch=dispatch, stop=stop)

    # ── Signature verification ────────────────────────────
    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        if channel_config.get("verify_signatures") is False:
            return
        sign_secret = channel_config.get("sign_secret")
        if not sign_secret:
            # Not configured → let the request through (back-compat
            # with channels created before fu-im-real hardening).
            return

        # DingTalk's custom-robot variant signs the query string of
        # the webhook URL, but our ingress route re-exposes the
        # provider-supplied ``timestamp`` / ``sign`` via headers we
        # accept either location.
        timestamp = headers.get("timestamp") or headers.get("x-dingtalk-timestamp")
        sign = headers.get("sign") or headers.get("x-dingtalk-sign")

        if not timestamp or not sign:
            raise SignatureInvalid(
                "dingtalk.missing_signature_headers",
                "DingTalk push missing 'timestamp' / 'sign' headers",
            )

        try:
            ts_ms = int(timestamp)
        except ValueError as e:
            raise SignatureInvalid("dingtalk.bad_timestamp", f"timestamp not an int: {e}") from e

        now_ms = int(time.time() * 1000)
        if abs(now_ms - ts_ms) > DINGTALK_TIMESTAMP_TOLERANCE_MS:
            raise SignatureInvalid(
                "dingtalk.stale_timestamp",
                f"timestamp outside the replay window ({ts_ms} vs {now_ms})",
            )

        expected = _compute_sign(timestamp, sign_secret)
        if not hmac.compare_digest(expected, sign):
            raise SignatureInvalid(
                "dingtalk.signature_mismatch",
                "DingTalk HMAC signature did not match",
            )

    # ── Inbound parsing ──────────────────────────────────
    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        # DingTalk custom-robot push format:
        #     {
        #       "msgtype": "text",
        #       "text": {"content": "..."},
        #       "senderNick": "...",
        #       "senderStaffId": "...",
        #       "conversationId": "...",
        #       "chatbotUserId": "...",
        #     }
        # We only handle ``text`` — rich types (markdown / image) fall
        # through to "ignore" so the agent doesn't try to respond with
        # garbage.
        msgtype = payload.get("msgtype")
        if msgtype != "text":
            return None

        text_block = payload.get("text") or {}
        user_text = str(text_block.get("content") or "").strip()
        if not user_text:
            return None

        sender = payload.get("senderNick") or payload.get("senderStaffId") or "unknown"
        thread_key = build_thread_key(
            conversation_type=payload.get("conversationType"),
            conversation_id=payload.get("conversationId"),
            sender_staff_id=payload.get("senderStaffId"),
        )

        return InboundMessage(
            thread_key=thread_key,
            user_text=user_text,
            external_user=str(sender),
            raw=payload,
        )

    # ── Outbound reply ───────────────────────────────────
    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        """Send a reply back to DingTalk.

        Stream mode (default) and webhook mode are two unrelated
        transports — the *outbound* paths are unrelated too. This
        method picks one based on what the operator stored in
        ``channel_config``:

        * Stream mode → operator stored ``client_id`` + ``client_secret``.
          We fetch (and cache) an OPEN-API access token and call
          ``oToMessages/batchSend`` (1:1) or ``groupMessages/send``
          (group), routed by the prefix encoded into ``thread_key``.

        * Webhook mode → operator stored ``webhook_url`` (+ optional
          ``sign_secret``). We POST a signed custom-robot payload back
          to the same URL; for groups that's all DingTalk supports,
          for 1:1 the bot has to be added to the chat first.

        If neither set is present (misconfigured channel), we log and
        drop the reply rather than raising — the runtime is invoked
        from a background task and a noisy raise just kills the loop.
        """
        client_id = (channel_config.get("client_id") or "").strip()
        client_secret = (channel_config.get("client_secret") or "").strip()
        webhook_url = (channel_config.get("webhook_url") or "").strip()

        if client_id and client_secret:
            await _send_via_openapi(
                client_id=client_id,
                client_secret=client_secret,
                thread_key=thread_key,
                text=text,
            )
            return

        if webhook_url:
            await _send_via_custom_robot(
                webhook_url=webhook_url,
                sign_secret=channel_config.get("sign_secret"),
                text=text,
            )
            return

        log.warning(
            "dingtalk channel has neither client_id/secret nor webhook_url; "
            "dropping reply (thread_key=%s)",
            thread_key,
        )


# ─── Helpers ─────────────────────────────────────────────
def _compute_sign(timestamp: str, secret: str) -> str:
    """Compute DingTalk's ``{timestamp}\\n{secret}`` HMAC-SHA256 signature.

    The signature is base64-encoded then URL-encoded by the caller
    (DingTalk's webhook URL format expects it that way).
    """
    string_to_sign = f"{timestamp}\n{secret}".encode()
    key = secret.encode("utf-8")
    digest = hmac.new(key, string_to_sign, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


# Single-flight token cache: DingTalk OPEN-API tokens live ~2h and the
# refresh endpoint is rate-limited, so we keep a per-(client_id,
# client_secret) cache in memory. Single-process deployments stay
# correct; multi-worker prod should swap this for Redis (Phase 2).
_OPENAPI_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_OPENAPI_TOKEN_REFRESH_EARLY_SEC = 60  # refresh 60s before DingTalk's expireIn


async def _fetch_openapi_access_token(
    client_id: str,
    client_secret: str,
) -> str | None:
    """Fetch (and cache) a DingTalk OPEN-API access token.

    The token is what stream-mode bots use to call ``robot/*`` send
    endpoints. We cache for ``expireIn - 60s`` to dodge clock skew
    around expiry.
    """
    key = (client_id, client_secret)
    now = time.time()
    cached = _OPENAPI_TOKEN_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]

    url = f"{DINGTALK_OPENAPI_BASE}/v1.0/oauth2/accessToken"
    body = {"appKey": client_id, "appSecret": client_secret}
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.post(url, json=body)
    except httpx.HTTPError as e:  # pragma: no cover - network path
        log.exception("dingtalk accessToken network error: %s", e)
        return None

    if resp.status_code >= 300:
        log.warning("dingtalk accessToken HTTP %s: %s", resp.status_code, resp.text[:200])
        return None

    data = resp.json() if resp.content else {}
    token = data.get("accessToken")
    if not token:
        log.warning("dingtalk accessToken missing in response: %s", data)
        return None
    expire_in = int(data.get("expireIn", 7200))
    _OPENAPI_TOKEN_CACHE[key] = (
        token,
        now + max(60, expire_in - _OPENAPI_TOKEN_REFRESH_EARLY_SEC),
    )
    return token


def _parse_thread_key(thread_key: str) -> tuple[str, str] | None:
    """Decode the prefix-encoded thread_key built by :func:`build_thread_key`.

    Returns ``("group", openConversationId)`` or ``("user", staffId)``
    or ``None`` for unrecognised shapes (the caller logs + drops).
    """
    if thread_key.startswith(_THREAD_KEY_PREFIX_GROUP):
        return "group", thread_key[len(_THREAD_KEY_PREFIX_GROUP) :]
    if thread_key.startswith(_THREAD_KEY_PREFIX_USER):
        return "user", thread_key[len(_THREAD_KEY_PREFIX_USER) :]
    return None


async def _send_via_openapi(
    *,
    client_id: str,
    client_secret: str,
    thread_key: str,
    text: str,
) -> None:
    """Stream-mode outbound: OPEN-API ``robot/*`` send endpoints.

    DingTalk's OPEN-API splits the "send a reply" call into two
    endpoints based on chat shape — there's no single send-anywhere
    URL. We dispatch by the prefix encoded into ``thread_key`` at
    inbound parse time.
    """
    parsed = _parse_thread_key(thread_key)
    if parsed is None:
        log.warning(
            "dingtalk openapi: malformed thread_key %r — cannot route reply",
            thread_key,
        )
        return

    target_kind, target_id = parsed
    if not target_id:
        log.warning("dingtalk openapi: thread_key %r has empty target id", thread_key)
        return

    token = await _fetch_openapi_access_token(client_id, client_secret)
    if not token:
        return

    # ``msgKey`` + ``msgParam`` is the OPEN-API equivalent of the
    # custom-robot "msgtype + body" pair. ``sampleMarkdown`` is the
    # most permissive layout; falls back to plain text if the client
    # can't render markdown.
    msg_param = json.dumps(
        {"text": text[:5000], "title": "SenHarness Reply"},
        ensure_ascii=False,
    )
    if target_kind == "group":
        url = f"{DINGTALK_OPENAPI_BASE}/v1.0/robot/groupMessages/send"
        payload: dict[str, Any] = {
            "robotCode": client_id,
            "openConversationId": target_id,
            "msgKey": "sampleMarkdown",
            "msgParam": msg_param,
        }
    else:  # user
        url = f"{DINGTALK_OPENAPI_BASE}/v1.0/robot/oToMessages/batchSend"
        payload = {
            "robotCode": client_id,
            "userIds": [target_id],
            "msgKey": "sampleMarkdown",
            "msgParam": msg_param,
        }

    headers = {"x-acs-dingtalk-access-token": token}
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:  # pragma: no cover - network path
        log.exception("dingtalk openapi send network error: %s", e)
        return

    if resp.status_code >= 300:
        log.warning(
            "dingtalk openapi send HTTP %s (%s): %s",
            resp.status_code,
            target_kind,
            resp.text[:300],
        )
        return

    body = resp.json() if resp.content else {}
    errcode = body.get("errcode") or body.get("code")
    if errcode not in (None, 0, "0"):
        log.warning(
            "dingtalk openapi send api-error (%s) errcode=%s body=%s",
            target_kind,
            errcode,
            resp.text[:300],
        )


async def _send_via_custom_robot(
    *,
    webhook_url: str,
    sign_secret: str | None,
    text: str,
) -> None:
    """Webhook-mode outbound: signed POST to the custom-robot URL."""
    url = webhook_url
    if sign_secret:
        timestamp = str(int(time.time() * 1000))
        sign = _compute_sign(timestamp, sign_secret)
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}timestamp={timestamp}&sign={quote_plus(sign)}"

    payload = {
        "msgtype": "markdown",
        "markdown": {"title": "SenHarness", "text": text},
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.post(url, json=payload)
        if resp.status_code >= 300:
            log.warning(
                "dingtalk custom-robot HTTP %s: %s",
                resp.status_code,
                resp.text[:200],
            )
    except httpx.HTTPError as e:  # pragma: no cover - network path
        log.exception("dingtalk custom-robot send failed: %s", e)
