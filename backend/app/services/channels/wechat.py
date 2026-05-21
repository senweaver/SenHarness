"""WeChat (iLink Bot) — personal WeChat via Tencent's iLink long-poll API.

The WeChat Open Platform doesn't ship a webhook-pushable bot for personal
accounts, so iLink fills the gap: an operator scans a QR code with their
personal WeChat, gets a Bearer token, and SenHarness then long-polls
``ilinkai.weixin.qq.com`` for new messages and posts replies through the
same REST surface.

That means:

* No public IP / domain required — the SenHarness process always
  initiates the HTTPS calls.
* The default mode is ``stream`` (long-poll loop).
* Webhook mode is supported as a degraded path for operators running
  their own relay that translates iLink events to a simple JSON
  ``{from_user_id, text, ...}`` payload.

Config shape (config_json):

    bot_token   (required)  — the iLink Bearer token returned after the
                              QR-login flow. Treat as a secret; rotates
                              when the operator re-scans.
    bot_uin     (optional)  — the bot's WeChat UIN; iLink derives it
                              from the token when omitted.
    relay_token (optional)  — for webhook-mode operators who put a
                              relay between iLink and SenHarness.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

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

ILINK_BASE = "https://ilinkai.weixin.qq.com"


class WeChatProvider(ChannelProvider):
    kind = "wechat"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="WeChat (iLink Bot)",
            description=(
                "Personal WeChat via Tencent's iLink Bot. The operator "
                "scans a QR code once to bind their account; SenHarness "
                "then long-polls iLink and replies via the same REST "
                "surface — no public domain required."
            ),
            docs_url="https://ilinkai.weixin.qq.com/",
            # Stream mode is the primary path: operators don't fill the
            # bot_token by hand, the QR-login flow writes it back into
            # config_json after a successful scan. So nothing is globally
            # required — see ``mode_required_fields["webhook"]`` for the
            # relay-mode contract that does demand a token.
            required_config_fields=(),
            optional_config_fields=(
                "bot_token",
                "bot_uin",
                "relay_token",
                "verify_signatures",
            ),
            supports_outbound=True,
            supported_modes=("webhook", "stream"),
            default_mode="stream",
            stream_requires_extra=None,
            mode_required_fields={
                # Stream mode = scan-to-bind, no fields required upfront.
                "stream": (),
                # Webhook mode is the relay path — operators run their
                # own iLink poller and forward into SenHarness, so they
                # already have a bot_token to paste.
                "webhook": ("bot_token",),
            },
            mode_optional_fields={
                "stream": ("bot_uin",),
                "webhook": ("relay_token", "bot_uin"),
            },
        )

    @classmethod
    def supports_stream(cls) -> bool:
        # iLink stream uses plain ``httpx`` long-poll — no extra deps.
        return True

    @classmethod
    def stream_available(cls) -> bool:
        return True

    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        if channel_config.get("verify_signatures") is False:
            return
        # Webhook mode is the "external relay" path — operators who run
        # their own iLink poller and want to forward into SenHarness via
        # a webhook should set ``relay_token`` and pass it as
        # ``X-Relay-Token``. Skipping the check is allowed for back-compat.
        relay = channel_config.get("relay_token")
        if not relay:
            return
        h = {k.lower(): v for k, v in headers.items()}
        supplied = h.get("x-relay-token") or ""
        if supplied != relay:
            raise SignatureInvalid(
                "wechat.relay_token_mismatch",
                "WeChat iLink relay token mismatch",
            )

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        # The simplified webhook-relay shape:
        #   {"from_user_id": "...", "text": "...", "context_token"?: "..."}
        text = str(payload.get("text") or "").strip()
        from_user = (
            payload.get("from_user_id")
            or payload.get("from_user")
            or payload.get("from")
            or ""
        )
        if not text or not from_user:
            return None

        context_token = str(payload.get("context_token") or "").strip()
        session = str(payload.get("session_id") or from_user).strip()
        # Same key layout as the stream path so ``post_reply`` reads
        # one shape: ``wechat:<from_user>:<context_token>[:<session>]``.
        thread = f"wechat:{from_user}:{context_token}:{session}"
        return InboundMessage(
            thread_key=thread,
            user_text=text,
            external_user=str(from_user),
            raw={
                "context_token": context_token,
                "msg_id": payload.get("msg_id"),
                "from_user_id": str(from_user),
                "session_id": session,
            },
        )

    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        """Send an outbound iLink text message.

        Routing info travels through ``thread_key`` because the
        dispatcher's ``send_text`` contract doesn't carry the inbound
        envelope. We pack ``"wechat:<to_user>:<context_token>"`` (and
        sometimes a ``:<session>`` tail) at parse time; here we split
        it back out. iLink rejects sends without ``context_token`` so
        we drop replies that arrived through some other path
        (relay-mode webhook, hand-built thread key).
        """
        from app.services.channels._wechat_ilink import send_reply

        bot_token = str(channel_config.get("bot_token") or "").strip()
        base_url = str(channel_config.get("baseurl") or ILINK_BASE).strip()
        if not bot_token:
            log.warning("wechat reply: channel missing bot_token; dropping")
            return

        parts = thread_key.split(":")
        if len(parts) < 3 or parts[0] != "wechat":
            log.warning(
                "wechat reply: thread_key %r has no context_token; dropping",
                thread_key,
            )
            return
        to_user = parts[1].strip()
        context_token = parts[2].strip()
        if not to_user or not context_token:
            log.warning(
                "wechat reply: missing to_user or context_token in %r; dropping",
                thread_key,
            )
            return

        try:
            await send_reply(
                bot_token=bot_token,
                base_url=base_url,
                to_user_id=to_user,
                context_token=context_token,
                text=text,
            )
            log.info(
                "wechat reply ok: to=%s preview=%r",
                to_user,
                text[:80],
            )
        except Exception as e:  # pragma: no cover — network path
            log.exception("wechat reply send failed (to=%s): %s", to_user, e)

    async def send_processing_indicator(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        # iLink's native typing indicator (the "对方正在输入中" tooltip
        # at the top of the WeChat chat header) is driven by two
        # calls: ``getconfig`` returns a ``typing_ticket``, then
        # ``sendtyping`` keeps it alive while we loop status=1 every
        # few seconds. The dispatcher cancels this coroutine the
        # moment the agent reply is ready, and the keepalive's
        # ``finally`` shields a status=2 to clear the indicator before
        # the real ``sendmessage`` lands.
        #
        # If ``getconfig`` is unavailable (older bot scope / network
        # failure) we degrade to the legacy one-shot placeholder text
        # so the user still sees activity. The text fallback is
        # disabled when the caller cleared the indicator template via
        # channel metadata (``text`` is empty).
        from app.services.channels._wechat_ilink import (
            fetch_typing_ticket,
            run_typing_keepalive,
            send_reply,
        )

        bot_token = str(channel_config.get("bot_token") or "").strip()
        base_url = str(channel_config.get("baseurl") or ILINK_BASE).strip()
        ilink_user_id = str(channel_config.get("ilink_user_id") or "").strip()
        if not bot_token:
            return
        parts = thread_key.split(":")
        if len(parts) < 3 or parts[0] != "wechat":
            return
        to_user = parts[1].strip()
        context_token = parts[2].strip()
        if not to_user or not context_token:
            return

        ticket = await fetch_typing_ticket(
            bot_token=bot_token,
            base_url=base_url,
            ilink_user_id=ilink_user_id,
        )
        if ticket:
            await run_typing_keepalive(
                bot_token=bot_token,
                base_url=base_url,
                ilink_user_id=ilink_user_id,
                to_user_id=to_user,
                typing_ticket=ticket,
            )
            return

        if not (text or "").strip():
            return
        try:
            await send_reply(
                bot_token=bot_token,
                base_url=base_url,
                to_user_id=to_user,
                context_token=context_token,
                text=text,
            )
        except Exception as e:  # pragma: no cover — network path
            log.warning("wechat processing indicator failed (to=%s): %s", to_user, e)

    async def run_stream(
        self,
        *,
        channel: Channel,
        dispatch: InboundDispatch,
        stop: asyncio.Event,
    ) -> None:
        from app.services.channels._wechat_ilink import run_long_poll

        await run_long_poll(channel=channel, dispatch=dispatch, stop=stop)
