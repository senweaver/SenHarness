"""WeCom AIBot WebSocket — thin wrapper over ``wecom-aibot-sdk-python``.

The community SDK is still 0.1.x; the migration point if it goes
unmaintained is right here — about 100 lines could replace it with a
direct WSS client to ``hwsapi.weixin.qq.com``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.services.channels.base import InboundDispatch, InboundMessage

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)


async def run_aibot_ws_stream(
    *,
    channel: Channel,
    dispatch: InboundDispatch,
    stop: asyncio.Event,
) -> None:
    try:
        from wecom_aibot_sdk import WSClient  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "wecom-aibot-sdk-python extra missing; install with "
            "'pip install \".[channels-stream]\"'"
        ) from e

    plain = getattr(channel, "_plain_config", None) or (channel.config_json or {})
    bot_id = str(plain.get("bot_id") or "").strip()
    bot_secret = str(plain.get("bot_secret") or plain.get("secret") or "").strip()
    if not (bot_id and bot_secret):
        log.info("wecom AIBot channel %s missing bot_id/secret — idle", channel.id)
        await stop.wait()
        return

    loop = asyncio.get_running_loop()

    def _on_message(frame: Any) -> None:
        try:
            text = (
                getattr(frame, "text", None)
                or (frame.get("text") if isinstance(frame, dict) else None)
                or ""
            ).strip()
            if not text:
                return
            conv_id = (
                getattr(frame, "conv_id", None)
                or (frame.get("conv_id") if isinstance(frame, dict) else None)
                or "wecom_aibot:unknown"
            )
            user = (
                getattr(frame, "from_user", None)
                or (frame.get("from_user") if isinstance(frame, dict) else None)
                or "wecom_user"
            )
            inbound = InboundMessage(
                thread_key=f"wecom_aibot:{conv_id}",
                user_text=text,
                external_user=str(user),
            )
            asyncio.run_coroutine_threadsafe(dispatch(inbound), loop)
        except Exception:  # pragma: no cover
            log.exception("wecom AIBot handler crashed")

    client = WSClient({"bot_id": bot_id, "secret": bot_secret})
    if hasattr(client, "on_message"):
        client.on_message(_on_message)  # type: ignore[attr-defined]

    import contextlib

    started = loop.run_in_executor(None, client.run_forever)
    try:
        await stop.wait()
    finally:
        with contextlib.suppress(Exception):
            client.stop()  # type: ignore[attr-defined]
        with contextlib.suppress(TimeoutError, Exception):
            await asyncio.wait_for(started, timeout=10.0)
