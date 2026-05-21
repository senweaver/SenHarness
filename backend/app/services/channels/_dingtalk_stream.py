"""DingTalk Stream Mode — wraps ``dingtalk-stream`` SDK.

Stream Mode is the modern DingTalk transport: the bot dials Alibaba's
gateway with ClientID + ClientSecret instead of letting them push to
a webhook URL. The official ``dingtalk-stream`` package handles the
WSS handshake and event multiplexing; we just register a callback for
``ChatbotMessage`` and translate it into our :class:`InboundMessage`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.services.channels.base import InboundDispatch, InboundMessage

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)


async def run_stream_client(
    *,
    channel: Channel,
    dispatch: InboundDispatch,
    stop: asyncio.Event,
) -> None:
    try:
        import dingtalk_stream
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "dingtalk-stream extra missing; install with "
            "'pip install \".[channels-stream]\"'"
        ) from e

    plain = getattr(channel, "_plain_config", None) or (channel.config_json or {})
    client_id = str(plain.get("client_id") or plain.get("app_key") or "").strip()
    client_secret = str(
        plain.get("client_secret") or plain.get("app_secret") or ""
    ).strip()
    if not (client_id and client_secret):
        log.info("dingtalk channel %s missing client_id/secret — idle", channel.id)
        await stop.wait()
        return

    credential = dingtalk_stream.Credential(client_id, client_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)

    # The SDK's ``start_forever()`` calls ``asyncio.run(self.start())``
    # inside a thread-pool executor, which spins up a brand-new event
    # loop in that thread. The handler's ``process()`` is awaited on
    # that loop, so any ``await`` we perform there runs on the wrong
    # loop — including SQLAlchemy/asyncpg connections that were attached
    # to uvicorn's main loop. The result is the
    #   "got Future <...> attached to a different loop"
    # crash on every inbound message.
    #
    # Mirror the lark/feishu pattern: capture the main loop here and
    # hop the dispatch coroutine back onto it via
    # ``asyncio.run_coroutine_threadsafe``. We ACK the gateway
    # immediately (DingTalk only gives us ~5s before it considers the
    # callback failed); the agent run + reply happens out-of-band on
    # the main loop.
    main_loop = asyncio.get_running_loop()

    class _Handler(dingtalk_stream.ChatbotHandler):
        async def process(self, callback: Any) -> Any:  # type: ignore[override]
            try:
                msg = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                text = (msg.text.content or "").strip() if msg.text else ""
                if not text:
                    return dingtalk_stream.AckMessage.STATUS_OK, "OK"

                # The thread_key has to round-trip through the dispatcher
                # back into ``post_reply`` — and post_reply needs to pick
                # *which* OPEN-API endpoint to call (group vs private). We
                # encode the routing decision in the key itself so the
                # outbound side stays stateless.
                from app.services.channels.dingtalk import build_thread_key

                thread_key = build_thread_key(
                    conversation_type=getattr(msg, "conversation_type", None),
                    conversation_id=getattr(msg, "conversation_id", None),
                    sender_staff_id=getattr(msg, "sender_staff_id", None),
                )

                inbound = InboundMessage(
                    thread_key=thread_key,
                    user_text=text,
                    external_user=str(
                        getattr(msg, "sender_nick", "")
                        or getattr(msg, "sender_staff_id", "")
                        or "dingtalk_user"
                    ),
                    raw={
                        "conversation_id": getattr(msg, "conversation_id", None),
                        "conversation_type": getattr(msg, "conversation_type", None),
                        "sender_staff_id": getattr(msg, "sender_staff_id", None),
                        "robot_code": getattr(msg, "robot_code", None),
                    },
                )
                # Fire-and-forget onto the main loop. We deliberately
                # don't ``await`` the future — DingTalk's gateway needs
                # the ACK back in a few seconds and the agent run can
                # easily exceed that.
                asyncio.run_coroutine_threadsafe(dispatch(inbound), main_loop)
            except Exception:  # pragma: no cover
                log.exception("dingtalk stream handler crashed")
            return dingtalk_stream.AckMessage.STATUS_OK, "OK"

    client.register_callback_handler(
        dingtalk_stream.ChatbotMessage.TOPIC, _Handler()
    )

    import contextlib

    started = main_loop.run_in_executor(None, client.start_forever)
    try:
        await stop.wait()
    finally:
        with contextlib.suppress(AttributeError):
            client.stop()  # type: ignore[attr-defined]
        with contextlib.suppress(TimeoutError, Exception):
            await asyncio.wait_for(started, timeout=10.0)
