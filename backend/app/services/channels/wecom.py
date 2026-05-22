"""WeChat Work / 企业微信 self-built app provider.

WeCom's "receive messages" webhook signs every push with an SHA1 of
(token + timestamp + nonce + encrypted_msg) and delivers the body
itself AES-encrypted (CBC, PKCS#7, random 16-byte IV prefix). Apps
also need to respond to a one-time ``VerifyURL`` GET request with
the decrypted echo string before WeCom will enable the callback URL.

This provider implements both paths:

    * ``verify_signature``  — SHA1 check of every inbound.
    * ``handshake_response`` — if the request is the ``VerifyURL``
      ping (identified by an ``echostr`` query param surfaced as the
      ``"_echostr"`` field we inject at the ingress layer), decrypt
      + re-encrypt-as-needed and return the plain echo string.
    * ``parse_inbound`` — decrypt the push body, pull the user's
      text + sender's UserID + chat/thread stability key.
    * ``post_reply`` — call the app's ``message/send`` REST API
      using the access_token we fetch on demand + cache for the
      2-hour WeCom token lifetime.

Reference:
  * https://developer.work.weixin.qq.com/document/path/90930 (callback)
  * https://developer.work.weixin.qq.com/document/path/90372 (sending)
  * https://developer.work.weixin.qq.com/document/path/96211 (crypto)

Config shape (config_json):

    corp_id           (required)  — the tenant's Corp ID
    agent_id          (required)  — numeric self-built-app ID
    secret            (required)  — the app secret (for token fetch)
    token             (required)  — message-callback token
    encoding_aes_key  (required)  — 43-char base64 key (without
                                    padding) the admin UI hands out
    verify_signatures (optional)  — default True
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import struct
import time
from typing import TYPE_CHECKING, Any

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


class WeComProvider(ChannelProvider):
    kind = "wecom"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="WeCom / 企业微信",
            description=(
                "WeChat Work self-built application or AIBot. Webhook "
                "mode uses SHA1 + AES-CBC inbound and message/send "
                "REST outbound. Stream mode (via wecom-aibot-sdk-python) "
                "lets the AIBot variant run without a public IP."
            ),
            docs_url="https://developer.work.weixin.qq.com/",
            required_config_fields=(
                "corp_id",
                "agent_id",
                "secret",
                "token",
                "encoding_aes_key",
            ),
            optional_config_fields=(
                "verify_signatures",
                "bot_id",
                "bot_secret",
            ),
            supports_outbound=True,
            supported_modes=("webhook", "stream"),
            default_mode="stream",
            stream_requires_extra="channels-stream",
            # Stream mode is the AIBot path (``bot_id`` + ``bot_secret``),
            # webhook mode is the self-built-app path (full 5-field
            # encrypted callback). They share nothing, so per-mode
            # overrides keep the form short on whichever path the
            # operator picks.
            mode_required_fields={
                "stream": ("bot_id", "bot_secret"),
                "webhook": (
                    "corp_id",
                    "agent_id",
                    "secret",
                    "token",
                    "encoding_aes_key",
                ),
            },
        )

    @classmethod
    def supports_stream(cls) -> bool:
        return True

    @classmethod
    def stream_available(cls) -> bool:
        try:
            import wecom_aibot_sdk  # noqa: F401
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
        from app.services.channels._wecom_stream import run_aibot_ws_stream

        await run_aibot_ws_stream(channel=channel, dispatch=dispatch, stop=stop)

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
        token = channel_config.get("token")
        if not token:
            return  # back-compat: skip when not configured

        msg_signature = headers.get("msg_signature") or headers.get("signature")
        timestamp = headers.get("timestamp") or ""
        nonce = headers.get("nonce") or ""
        # The encrypted body is either in a query-lifted field
        # (for VerifyURL ``echostr``) or in the JSON body under
        # ``"Encrypt"``. We hash whichever we've got.
        # The ingress is responsible for populating one of these on
        # the headers dict; if neither is present we fall back to
        # hashing the raw body which still rules out naive forgery.
        encrypt = (
            headers.get("_echostr")
            or headers.get("encrypt")
            or body.decode("utf-8", errors="replace")
        )
        if not msg_signature:
            raise SignatureInvalid(
                "wecom.missing_signature",
                "WeCom push missing msg_signature header",
            )

        parts = sorted([token, timestamp, nonce, encrypt])
        expected = hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()
        # SHA1 is weak for secrecy but WeCom uses it only for
        # message-integrity + timestamp-based replay defence; the
        # payload secrecy comes from the AES layer below.
        if expected != msg_signature:
            raise SignatureInvalid(
                "wecom.signature_mismatch",
                "WeCom msg_signature mismatch",
            )

    # ── Handshake ────────────────────────────────────────
    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        # The ingress route identifies VerifyURL pings by the
        # presence of ``_echostr`` (lifted from the query string).
        # When we see one, decrypt and return the plaintext.
        echo = payload.get("_echostr")
        if not echo:
            return None

        aes_key = payload.get("_aes_key")
        corp_id = payload.get("_corp_id")
        if not aes_key or not corp_id:
            # Ingress should have populated these from channel_config
            # before handing to us; if missing, surface as a 400-equiv
            # by returning None — the ingress will then 400.
            return None

        try:
            plain, receive_id = _aes_decrypt(echo, aes_key)
        except Exception as e:  # pragma: no cover - bad key / malformed
            log.warning("wecom verifyURL decrypt failed: %s", e)
            return None

        if receive_id != corp_id:
            log.warning(
                "wecom verifyURL corp_id mismatch: %s vs %s",
                receive_id,
                corp_id,
            )
            return None

        return {"_plain_text_response": plain}

    # ── Inbound parsing ──────────────────────────────────
    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        # WeCom posts an XML body containing an ``Encrypt`` field
        # (either as XML or re-wrapped JSON by our ingress). We
        # expect the ingress to have parsed it into a dict.
        encrypt = payload.get("Encrypt") or payload.get("encrypt")
        if not encrypt:
            return None
        aes_key = payload.get("_aes_key")
        corp_id = payload.get("_corp_id")
        if not aes_key or not corp_id:
            log.warning(
                "wecom parse_inbound missing aes_key / corp_id; ingress wasn't configured correctly"
            )
            return None

        try:
            plain, receive_id = _aes_decrypt(encrypt, aes_key)
        except Exception as e:  # pragma: no cover - malformed
            log.warning("wecom decrypt failed: %s", e)
            return None

        if receive_id != corp_id:
            log.warning(
                "wecom corp_id mismatch in payload: %s vs %s",
                receive_id,
                corp_id,
            )
            return None

        # ``plain`` is an XML string; we parse just the fields we need
        # without pulling in lxml. WeCom guarantees no CDATA nesting.
        from xml.etree import ElementTree as ET

        try:
            root = ET.fromstring(plain)
        except ET.ParseError as e:
            log.warning("wecom inbound XML parse failed: %s", e)
            return None

        msg_type = (root.findtext("MsgType") or "").strip()
        if msg_type != "text":
            return None  # ignore images / links / events for now

        content = (root.findtext("Content") or "").strip()
        if not content:
            return None

        from_user = (root.findtext("FromUserName") or "unknown").strip()
        # WeCom doesn't expose stable "thread" keys for 1-1 chats
        # — use the sender's UserID as the thread key so repeated
        # messages from the same user land in the same session.
        thread_key = from_user

        return InboundMessage(
            thread_key=thread_key,
            user_text=content,
            external_user=from_user,
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
        corp_id = channel_config.get("corp_id")
        secret = channel_config.get("secret")
        agent_id = channel_config.get("agent_id")
        if not corp_id or not secret or not agent_id:
            log.warning("wecom reply skipped — config incomplete")
            return

        token = await _fetch_access_token(corp_id, secret)
        if not token:
            return

        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": thread_key,
            "msgtype": "text",
            "agentid": int(agent_id),
            "text": {"content": text[:2000]},  # WeCom caps at 2048 chars
            "safe": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                resp = await cli.post(url, json=payload)
            if resp.status_code >= 300 or resp.json().get("errcode", 0) != 0:
                log.warning(
                    "wecom post_reply failed: %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except httpx.HTTPError as e:  # pragma: no cover
            log.exception("wecom post_reply network error: %s", e)

    async def send_processing_indicator(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        # WeCom offers no native typing surface; reuse the same
        # message/send REST endpoint and access-token cache as the
        # real reply path, but swallow errors — the indicator is
        # cosmetic and must not stall the agent run.
        corp_id = channel_config.get("corp_id")
        secret = channel_config.get("secret")
        agent_id = channel_config.get("agent_id")
        if not corp_id or not secret or not agent_id or not (text or "").strip():
            return
        token = await _fetch_access_token(corp_id, secret)
        if not token:
            return
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"
        payload = {
            "touser": thread_key,
            "msgtype": "text",
            "agentid": int(agent_id),
            "text": {"content": text[:2000]},
            "safe": 0,
        }
        try:
            async with httpx.AsyncClient(timeout=5.0) as cli:
                resp = await cli.post(url, json=payload)
            if resp.status_code >= 300 or resp.json().get("errcode", 0) != 0:
                log.warning(
                    "wecom processing indicator failed: %s — %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except httpx.HTTPError as e:  # pragma: no cover
            log.warning("wecom processing indicator network error: %s", e)


# ─── Helpers ─────────────────────────────────────────────
# Single-flight token cache: WeCom tokens live 2 hours and the
# endpoint is rate-limited, so we keep a per-(corp_id, secret) cache.
# Very small deployments won't race; multi-tenant SaaS should swap
# this for Redis caching (Phase 2 concern).
_TOKEN_CACHE: dict[tuple[str, str], tuple[str, float]] = {}
_TOKEN_TTL_SEC = 60 * 60  # refresh one hour before WeCom's 2h expiry


async def _fetch_access_token(corp_id: str, secret: str) -> str | None:
    """Fetch + cache an access_token for (corp_id, secret)."""
    key = (corp_id, secret)
    now = time.time()
    cached = _TOKEN_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]

    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corp_id}&corpsecret={secret}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as cli:
            resp = await cli.get(url)
    except httpx.HTTPError as e:  # pragma: no cover
        log.exception("wecom gettoken network error: %s", e)
        return None

    body = resp.json() if resp.status_code == 200 else {}
    token = body.get("access_token")
    if not token:
        log.warning("wecom gettoken failed: %s", body)
        return None
    _TOKEN_CACHE[key] = (token, now + _TOKEN_TTL_SEC)
    return token


def _aes_decrypt(ciphertext_b64: str, aes_key_b64: str) -> tuple[str, str]:
    """Decrypt a WeCom-style AES-CBC payload.

    Returns ``(plain_text, receive_id)`` where ``receive_id`` is the
    embedded corp_id used for a second-layer integrity check.

    The format is::

        random(16B) | msg_len(4B, big-endian) | msg | receive_id

    Padding is PKCS#7 (WeCom calls it "PKCS7" — standard).
    """
    try:
        from cryptography.hazmat.primitives.ciphers import (
            Cipher,
            algorithms,
            modes,
        )
    except ImportError as e:
        raise RuntimeError("WeCom provider requires the 'cryptography' package") from e

    # WeCom uses 43-char base64 keys (missing one char of padding).
    aes_key = base64.b64decode(aes_key_b64 + "=")
    if len(aes_key) != 32:
        raise ValueError(f"WeCom AES key must be 32 bytes, got {len(aes_key)}")

    ciphertext = base64.b64decode(ciphertext_b64)
    iv = aes_key[:16]

    cipher = Cipher(algorithms.AES(aes_key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    raw = decryptor.update(ciphertext) + decryptor.finalize()

    # Strip PKCS#7 padding — last byte is the pad length.
    pad_len = raw[-1]
    if not 1 <= pad_len <= 32:
        raise ValueError(f"WeCom AES pad length implausible: {pad_len}")
    raw = raw[:-pad_len]

    # Drop the 16-byte random prefix.
    body = raw[16:]

    # Next 4 bytes are the message length (network byte order).
    msg_len = struct.unpack(">I", body[:4])[0]
    msg = body[4 : 4 + msg_len].decode("utf-8")
    receive_id = body[4 + msg_len :].decode("utf-8")

    return msg, receive_id
