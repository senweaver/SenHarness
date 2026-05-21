"""Feishu / Lark stream support — wraps the official ``lark-oapi`` SDK.

Both ``feishu`` and ``lark`` providers funnel into here; the only
difference is the API domain (``open.feishu.cn`` vs
``open.larksuite.com``). The SDK ships its own WebSocket client, so
our job is to:

1. Map our :class:`InboundMessage` shape onto its ``EventDispatcher``.
2. Run ``client.start()`` on a dedicated thread with its **own**
   asyncio event loop — ``lark_oapi.ws.client`` does
   ``loop = asyncio.get_event_loop()`` at import time and reuses that
   module-level ``loop`` inside ``start()``. Calling it on the main
   thread (where uvicorn already owns a running loop) raises
   "This event loop is already running"; calling it via
   ``run_in_executor`` on the same loop has the same problem.
3. Translate stop signals from our :class:`asyncio.Event` into the
   thread's shutdown so cancellation propagates cleanly.

The SDK is an optional extra (``pip install '.[channels-stream]'``);
an :class:`ImportError` here surfaces to the runtime, which logs and
falls back to webhook mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
from typing import TYPE_CHECKING, Any

from app.services.channels.base import InboundDispatch, InboundMessage

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)


async def run_oapi_ws_stream(
    *,
    channel: Channel,
    dispatch: InboundDispatch,
    stop: asyncio.Event,
    domain: str = "feishu",
) -> None:
    """Open a lark-oapi WebSocket and pump events into ``dispatch``.

    ``domain`` selects between Feishu (``feishu``) and Lark
    (``lark``). The SDK auto-routes to the right region based on the
    ``LARK_DOMAIN`` env var; we override it per-call instead so the
    same process can host channels on both regions concurrently.
    """
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1  # noqa: F401
    except ImportError as e:  # pragma: no cover - SDK absent path
        raise RuntimeError(
            "lark-oapi extra missing; install with "
            "'pip install \".[channels-stream]\"'"
        ) from e

    plain = getattr(channel, "_plain_config", None) or (channel.config_json or {})
    app_id = str(plain.get("app_id") or "").strip()
    app_secret = str(plain.get("app_secret") or "").strip()
    if not (app_id and app_secret):
        log.info(
            "lark/feishu channel %s missing app_id/app_secret — staying idle",
            channel.id,
        )
        await stop.wait()
        return

    # Optional encrypt_key / verification_token only matter for the
    # webhook signature path; the WS gateway handles its own auth via
    # app credentials. We pass them anyway so card actions / encrypted
    # event bodies work if the operator turned encryption on in the
    # Feishu console.
    encrypt_key = str(plain.get("encrypt_key") or "").strip()
    verification_token = str(plain.get("verification_token") or "").strip()

    main_loop = asyncio.get_running_loop()

    def _handle_message_receive(event: Any) -> None:
        """Translate an SDK event into our :class:`InboundMessage`.

        Runs on the SDK's own thread; we hop back to the main loop via
        ``run_coroutine_threadsafe`` so the dispatch coroutine awaits
        in the right place.
        """
        try:
            msg = event.event.message
            sender = event.event.sender
            content_raw = msg.content or "{}"
            try:
                content = (
                    json.loads(content_raw)
                    if isinstance(content_raw, str)
                    else content_raw
                )
            except json.JSONDecodeError:
                content = {}
            text = (content.get("text") or "").strip()
            if not text:
                return
            chat_id = msg.chat_id or "unknown"
            root_id = msg.root_id or msg.message_id or ""
            sender_id = "lark_user"
            sid = getattr(sender, "sender_id", None) if sender else None
            if sid is not None:
                sender_id = (
                    getattr(sid, "open_id", None)
                    or getattr(sid, "user_id", None)
                    or "lark_user"
                )
            inbound = InboundMessage(
                thread_key=f"{domain}:{chat_id}:{root_id}",
                user_text=text,
                external_user=str(sender_id),
                raw={
                    "chat_id": chat_id,
                    "message_id": msg.message_id,
                    "root_id": root_id,
                },
            )
            asyncio.run_coroutine_threadsafe(dispatch(inbound), main_loop)
        except Exception:  # pragma: no cover
            log.exception("lark stream handler crashed")

    handler = (
        lark.EventDispatcherHandler.builder(encrypt_key, verification_token)
        .register_p2_im_message_receive_v1(_handle_message_receive)
        .build()
    )

    domain_const = lark.FEISHU_DOMAIN if domain == "feishu" else lark.LARK_DOMAIN

    # ``lark.ws.Client`` is a positional-arg constructor (NOT a builder).
    # ``auto_reconnect=True`` lets the SDK ride out transient WS drops
    # without bouncing back through our outer reconnect loop.
    client: Any = lark.ws.Client(
        app_id,
        app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
        domain=domain_const,
        auto_reconnect=True,
    )

    # The SDK reaches for ``asyncio.get_event_loop()`` at module
    # import time and stores the result in ``lark_oapi.ws.client.loop``;
    # ``Client.start()`` then schedules its own coroutines on that
    # captured loop. If we run ``start()`` on the main thread the loop
    # is already running and we hit "This event loop is already
    # running"; if we run it via ``run_in_executor`` the executor
    # thread has no loop. Either way it fails to connect, and the
    # outer runtime reports a generic disconnect.
    #
    # Reproduce the working pattern: spin a dedicated thread that owns
    # a brand-new asyncio loop and patch the SDK's module-level
    # ``loop`` to point at it. The SDK's blocking ``start()`` call
    # then has a real loop to drive its WebSocket coroutines on.
    ws_thread_started = threading.Event()
    ws_thread_error: dict[str, BaseException] = {}

    def _run_ws() -> None:
        try:
            import lark_oapi.ws.client as _ws_module

            ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(ws_loop)
            _ws_module.loop = ws_loop
            ws_thread_started.set()
            try:
                # ``start()`` blocks for the lifetime of the WS
                # connection; with auto_reconnect=True it will only
                # return on a hard auth failure or on stop().
                client.start()
            finally:
                with contextlib.suppress(Exception):
                    ws_loop.close()
        except BaseException as exc:  # pragma: no cover - thread-only
            ws_thread_error["error"] = exc
            ws_thread_started.set()

    ws_thread = threading.Thread(
        target=_run_ws,
        name=f"lark-ws-{domain}-{str(channel.id)[:8]}",
        daemon=True,
    )
    ws_thread.start()

    # Wait for the thread to either reach client.start() or fail loop
    # setup. Without this the supervisor can't tell a credential typo
    # apart from a hung start.
    await asyncio.get_running_loop().run_in_executor(
        None, ws_thread_started.wait, 5.0
    )
    if "error" in ws_thread_error:
        raise RuntimeError(str(ws_thread_error["error"]))

    try:
        await stop.wait()
    finally:
        with contextlib.suppress(Exception):
            client.stop()
        # Poll the daemon thread asynchronously instead of submitting
        # ``thread.join`` into the loop's default executor. The latter
        # pollutes the executor pool during shutdown — asyncio's
        # ``loop.shutdown_default_executor`` then blocks for its 300s
        # default while waiting for the join to drain, freezing the
        # entire uvicorn worker (and every other tenant's traffic with
        # it). The daemonised thread will be reaped on interpreter
        # exit if it doesn't honour ``client.stop()`` in time.
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 2.0
        while ws_thread.is_alive():
            if loop.time() >= deadline:
                log.warning(
                    "lark/feishu ws thread for channel %s did not exit in 2s; "
                    "leaving as daemon",
                    channel.id,
                )
                break
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                break

    if "error" in ws_thread_error:  # pragma: no cover - defensive
        # Surfacing the underlying error lets the runtime backoff loop
        # show a useful ``last_error`` instead of "Disconnected".
        raise RuntimeError(str(ws_thread_error["error"]))
