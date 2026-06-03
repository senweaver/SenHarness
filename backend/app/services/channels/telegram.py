"""Telegram bot webhook provider.

Inbound security:
Telegram can attach ``X-Telegram-Bot-Api-Secret-Token`` on every push.
When ``config_json.secret_token`` is set, we require an exact match.

Inbound payload:
Handles standard ``message`` / ``edited_message`` updates with text.

Outbound:
Replies through Bot API ``sendMessage`` using ``config_json.bot_token``.
"""

from __future__ import annotations

import logging
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


class TelegramProvider(ChannelProvider):
    kind = "telegram"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name="Telegram",
            description=(
                "Telegram bot webhook with optional secret-token "
                "header validation and Bot API outbound reply."
            ),
            docs_url="https://core.telegram.org/bots/api",
            required_config_fields=("bot_token",),
            optional_config_fields=("secret_token", "verify_signatures"),
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
        expected = str(channel_config.get("secret_token") or "").strip()
        if not expected:
            return

        supplied = (
            headers.get("x-telegram-bot-api-secret-token")
            or headers.get("X-Telegram-Bot-Api-Secret-Token")
            or ""
        ).strip()
        if not supplied:
            raise SignatureInvalid(
                "telegram.missing_secret_token",
                "missing Telegram secret token header",
            )
        if supplied != expected:
            raise SignatureInvalid(
                "telegram.secret_token_mismatch",
                "Telegram secret token mismatch",
            )

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        msg = payload.get("message") or payload.get("edited_message") or {}
        text = str(msg.get("text") or "").strip()
        if not text:
            return None

        chat = msg.get("chat") or {}
        from_user = msg.get("from") or {}

        chat_id = chat.get("id")
        if chat_id is None:
            return None
        message_thread_id = msg.get("message_thread_id")
        if message_thread_id is not None:
            thread_key = f"telegram:{chat_id}:{message_thread_id}"
        else:
            thread_key = f"telegram:{chat_id}"

        external_user = (
            from_user.get("username")
            or from_user.get("first_name")
            or str(from_user.get("id") or "telegram_user")
        )

        return InboundMessage(
            thread_key=thread_key,
            user_text=text,
            external_user=str(external_user),
            raw={
                "update_id": payload.get("update_id"),
                "message_id": msg.get("message_id"),
                "chat_id": chat_id,
                "from_id": from_user.get("id"),
            },
        )

    async def post_reply(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        bot_token = str(channel_config.get("bot_token") or "").strip()
        if not bot_token:
            log.warning("telegram channel missing bot_token; skipping reply")
            return

        chat_id, message_thread_id = _parse_thread_key(thread_key)
        if chat_id is None:
            log.warning("malformed telegram thread_key %r", thread_key)
            return

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        await self._send_payload(bot_token, payload)

    async def send_message(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        message: OutboundMessage,
    ) -> None:
        """Rich Telegram send: agent menu as an inline keyboard.

        Telegram bots can't change their display name per message, so the
        ``identity`` hint is ignored (attribution stays text-prefixed by
        the presenter). Buttons render as a one-per-row inline keyboard
        whose ``callback_data`` is the menu number.
        """
        bot_token = str(channel_config.get("bot_token") or "").strip()
        if not bot_token:
            log.warning("telegram channel missing bot_token; skipping reply")
            return
        chat_id, message_thread_id = _parse_thread_key(thread_key)
        if chat_id is None:
            log.warning("malformed telegram thread_key %r", thread_key)
            return

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": message.text[:3900],
            "disable_web_page_preview": True,
        }
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id
        if message.buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": b.label[:64], "callback_data": b.value}]
                    for b in message.buttons
                ]
            }
        await self._send_payload(bot_token, payload)

    async def _send_payload(self, bot_token: str, payload: dict[str, Any]) -> None:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=10.0) as cli:
                resp = await cli.post(url, json=payload)
            if resp.status_code >= 300:
                log.warning(
                    "telegram sendMessage HTTP %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
        except httpx.HTTPError as e:  # pragma: no cover - network path
            log.exception("telegram sendMessage failed: %s", e)


def _parse_thread_key(thread_key: str) -> tuple[int | None, int | None]:
    parts = thread_key.split(":")
    if len(parts) < 2 or parts[0] != "telegram":
        return None, None
    try:
        chat_id = int(parts[1])
    except ValueError:
        return None, None
    if len(parts) >= 3:
        try:
            return chat_id, int(parts[2])
        except ValueError:
            return chat_id, None
    return chat_id, None
