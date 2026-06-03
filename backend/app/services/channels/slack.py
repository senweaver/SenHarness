"""Slack Events API provider.

Handles:
    * ``url_verification`` handshake → returns the challenge.
    * ``event_callback`` with inner event ``message`` (plus direct ``app_mention``).

Outbound: ``chat.postMessage`` using the bot token stored in the channel's
``config_json.bot_token``. Reply is threaded when the inbound event had a
``thread_ts``; otherwise top-level in the same ``channel``.

Inbound auth: ``v0`` HMAC signature (X-Slack-Signature + X-Slack-Request-Timestamp)
with the ``signing_secret`` stored in ``config_json.signing_secret``. Requests
older than 5 minutes are rejected to block replay.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.services.channels.base import (
    ChannelProvider,
    ChannelProviderMeta,
    InboundMessage,
    OutboundMessage,
    SignatureInvalid,
)

log = logging.getLogger(__name__)

SLACK_TIMESTAMP_TOLERANCE_SEC = 60 * 5


class SlackProvider(ChannelProvider):
    kind = "slack"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Slack",
            description=(
                "Slack workspace bot. Accepts Events API webhooks and "
                "replies via chat.postMessage. Requires a bot token and "
                "the signing secret."
            ),
            docs_url="https://api.slack.com/events-api",
            required_config_fields=("bot_token", "signing_secret"),
            optional_config_fields=(
                "team_id",
                "verify_signatures",
                "expected_team_id",
            ),
            supports_outbound=True,
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
        signing_secret = channel_config.get("signing_secret")
        if not signing_secret:
            # No secret configured → signature check is off (back-compat for
            # channels created before this feature landed).
            return
        # Header names are case-insensitive; normalize to lower.
        h = {k.lower(): v for k, v in headers.items()}
        sig = h.get("x-slack-signature")
        ts = h.get("x-slack-request-timestamp")
        if not sig or not ts:
            raise SignatureInvalid("slack.missing_headers", "missing Slack signature headers")
        try:
            ts_int = int(ts)
        except ValueError as e:
            raise SignatureInvalid("slack.bad_timestamp", "bad timestamp") from e
        # Replay window: 5 minutes by default per Slack docs.
        if abs(int(time.time()) - ts_int) > SLACK_TIMESTAMP_TOLERANCE_SEC:
            raise SignatureInvalid(
                "slack.timestamp_skew",
                "timestamp outside replay window",
            )
        basestring = b"v0:" + ts.encode() + b":" + body
        digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
        expected = f"v0={digest}"
        if not hmac.compare_digest(expected, sig):
            raise SignatureInvalid("slack.bad_signature", "Slack signature mismatch")

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        event_type = payload.get("type")
        if event_type == "url_verification":
            return None

        if event_type != "event_callback":
            return None

        ev = payload.get("event") or {}
        subtype = ev.get("subtype")
        # Ignore bot echoes, message_deleted, message_changed, etc.
        if subtype and subtype not in {"file_share"}:
            return None
        if ev.get("bot_id"):
            return None

        text = (ev.get("text") or "").strip()
        if not text:
            return None

        channel_id = ev.get("channel") or payload.get("team_id") or "unknown"
        thread_ts = ev.get("thread_ts") or ev.get("ts") or ""
        user = ev.get("user") or "slack_user"

        return InboundMessage(
            thread_key=f"slack:{channel_id}:{thread_ts}",
            user_text=text,
            external_user=user,
            raw={
                "channel": channel_id,
                "thread_ts": thread_ts,
                "ts": ev.get("ts"),
                "team_id": payload.get("team_id"),
                "event_id": payload.get("event_id"),
            },
        )

    def assert_team_id(
        self,
        *,
        channel_config: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Optional Slack workspace pinning.

        Set ``expected_team_id`` on the channel config to lock inbound
        traffic to one Slack team — protects against credential theft
        where the same signing secret happens to authenticate two
        different installs. Raises :class:`SignatureInvalid` so the
        ingress translates the rejection into HTTP 403 and the audit
        layer records ``channel.slack_team_mismatch``.
        """
        expected = (channel_config or {}).get("expected_team_id")
        if not expected:
            return
        actual = payload.get("team_id")
        if actual != expected:
            raise SignatureInvalid(
                "slack.team_id_mismatch",
                f"team_id mismatch: expected={expected!r} got={actual!r}",
            )

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}
        return None

    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        bot_token = channel_config.get("bot_token")
        if not bot_token:
            log.warning("slack channel has no bot_token; skipping reply")
            return

        # thread_key is "slack:<channel_id>:<thread_ts>" — parse back out.
        try:
            _, channel_id, thread_ts = thread_key.split(":", 2)
        except ValueError:
            log.warning("malformed slack thread_key %r", thread_key)
            return

        body: dict[str, Any] = {"channel": channel_id, "text": text[:39000]}
        if thread_ts:
            body["thread_ts"] = thread_ts
        await self._post_message(channel_config, body)

    async def send_message(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        message: OutboundMessage,
    ) -> None:
        """Rich Slack send: per-message bot identity + quick-reply buttons.

        Slack ``chat.postMessage`` lets us override the bot's display name
        per message (``username``) for ``reply_attribution=identity``, and
        render the agent menu as a Block Kit ``actions`` row of buttons
        (a tap posts the menu number back, same as typing it). Falls back
        to plain text when neither is present.
        """
        bot_token = channel_config.get("bot_token")
        if not bot_token:
            log.warning("slack channel has no bot_token; skipping reply")
            return
        try:
            _, channel_id, thread_ts = thread_key.split(":", 2)
        except ValueError:
            log.warning("malformed slack thread_key %r", thread_key)
            return

        body: dict[str, Any] = {"channel": channel_id, "text": message.text[:39000]}
        if thread_ts:
            body["thread_ts"] = thread_ts
        if message.identity and message.identity.get("name"):
            body["username"] = str(message.identity["name"])[:80]
        if message.buttons:
            body["blocks"] = [
                {"type": "section", "text": {"type": "mrkdwn", "text": message.text[:3000]}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": b.label[:75]},
                            "value": b.value,
                            "action_id": f"agent_select_{b.value}",
                        }
                        for b in message.buttons[:5]
                    ],
                },
            ]
        await self._post_message(channel_config, body)

    async def _post_message(
        self, channel_config: dict[str, Any], body: dict[str, Any]
    ) -> None:
        bot_token = channel_config.get("bot_token")
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.post(
                    "https://slack.com/api/chat.postMessage",
                    json=body,
                    headers={
                        "Authorization": f"Bearer {bot_token}",
                        "Content-Type": "application/json; charset=utf-8",
                    },
                )
                data = r.json() if r.content else {}
                if not data.get("ok"):
                    log.warning("slack postMessage failed: %s", data)
        except Exception as e:  # pragma: no cover
            log.warning("slack reply error: %s", e)
