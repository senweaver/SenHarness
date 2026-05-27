"""WeChat iLink Bot — QR login + HTTP long-poll.

iLink is the Tencent service that lets a *personal* WeChat account act
as a bot. There's no SDK; the protocol is plain HTTPS:

    GET  /ilink/bot/get_bot_qrcode      ← get QR for the operator to scan
    GET  /ilink/bot/get_qrcode_status   ← poll until they confirm
    POST /ilink/bot/getupdates          ← long-poll for inbound messages
    POST /ilink/bot/sendmessage         ← outbound reply

Wire formats discovered from a working production reference:

* **get_bot_qrcode** is GET (no auth pre-bind). Returns
  ``{"qrcode": "<id>", "qrcode_img_content": "<image URL>", ...}``.
  The id is the cursor used by the polling endpoint; the
  ``qrcode_img_content`` is a public HTTPS URL (e.g. on
  ``liteapp.weixin.qq.com``) that the browser loads directly — we pass
  it through unchanged so the frontend can render and copy it without
  the backend proxying image bytes.

* **get_qrcode_status** is GET, takes ``?qrcode=<id>`` and the header
  ``iLink-App-ClientVersion: 1``. Returns
  ``{"status": "wait"|"scanned"|"confirmed"|"expired", "bot_token":
  "...", "ilink_user_id": "...", "ilink_bot_id": "...", "baseurl":
  "..."}``. Status is a string, not an integer.

* **getupdates** is POST with ``Authorization: Bearer <bot_token>``,
  ``AuthorizationType: ilink_bot_token``, ``X-WECHAT-UIN: <random>``.
  Body: ``{"get_updates_buf": cursor, "base_info": {channel_version}}``.
  Errors propagate via top-level ``ret`` / ``errcode``; ``-14`` means
  the session is expired.

We package those four calls behind two top-level surfaces:

* :func:`start_qr_login` / :func:`poll_qr_login` — used by the
  frontend dialog for one-time bot binding.
* :func:`run_long_poll` — used by ``WeChatProvider.run_stream`` to
  keep an active link alive.

The ``Channel`` row's plaintext config is reachable via the
``_plain_config`` attribute that :class:`ChannelRuntime` stamps on
the row before kicking off ``run_stream``. (We must not call back
into the secret box here because the runtime already paid that cost
once and re-running it on every long-poll iteration would multiply
the keyring traffic by N.)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import os
import time
import uuid as uuid_pkg
from typing import TYPE_CHECKING, Any

import httpx

from app.services.channels.base import (
    ChannelStreamAuthExpired,
    InboundDispatch,
    InboundMessage,
)

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)


_BASE = "https://ilinkai.weixin.qq.com"
_QR_TTL_SEC = 120
_LONG_POLL_TIMEOUT = 25.0  # iLink's hold-open ceiling
_LONG_POLL_INTERVAL = 1.5  # gap between polls when no updates
_CHANNEL_VERSION = "1.0.0"


# Shared outbound httpx client for iLink REST calls
# (``sendmessage`` / ``sendtyping`` / ``getconfig``). Each fresh
# ``httpx.AsyncClient(...)`` paid a full TLS handshake to
# ``ilinkai.weixin.qq.com`` (~1.5s on a cold connection); reusing a
# single client across the lifetime of the process keeps the connection
# warm so subsequent calls only pay the request round-trip.
# Not used by the long-poll loop (it owns its own client with a longer
# read timeout) or the unauthenticated QR-login endpoints.
_OUTBOUND_CLIENT: httpx.AsyncClient | None = None
_OUTBOUND_CLIENT_LOCK = asyncio.Lock()


async def _get_outbound_client() -> httpx.AsyncClient:
    global _OUTBOUND_CLIENT
    if _OUTBOUND_CLIENT is not None and not _OUTBOUND_CLIENT.is_closed:
        return _OUTBOUND_CLIENT
    async with _OUTBOUND_CLIENT_LOCK:
        if _OUTBOUND_CLIENT is None or _OUTBOUND_CLIENT.is_closed:
            _OUTBOUND_CLIENT = httpx.AsyncClient(timeout=httpx.Timeout(20.0))
        return _OUTBOUND_CLIENT


# iLink's typing-indicator endpoints: one ``typing_ticket`` per bot_token,
# nominally valid 24h. We cap our cache an hour below that so a tail-of-day
# turn never re-uses a ticket the server already retired.
_TYPING_TICKET_TTL_SEC = 23 * 3600
_TYPING_TICKET_CACHE: dict[str, tuple[str, float]] = {}
_TYPING_TICKET_LOCK = asyncio.Lock()
_TYPING_KEEPALIVE_SEC = 5.0  # iLink loses the indicator after ~7s idle

# Process-wide dedup window for inbound message IDs. iLink uses at-least-once
# delivery on ``getupdates``: when the long-poll task restarts (transient
# HTTP blip, runtime supervisor reconcile, etc.) the in-memory ``cursor`` is
# lost and the server happily re-delivers any unacked messages. Without this
# the agent processes the same turn twice and the user receives duplicate
# replies. Keyed by channel id so two channels can't poison each other.
from collections import deque as _deque

_PROCESSED_MSG_IDS: dict[uuid_pkg.UUID, _deque] = {}
_PROCESSED_MSG_DEDUP_WINDOW = 256


def _random_uin() -> str:
    """Generate the ``X-WECHAT-UIN`` header value per the protocol."""
    value = int.from_bytes(os.urandom(4), "big", signed=False)
    return base64.b64encode(str(value).encode("utf-8")).decode("utf-8")


def _build_auth_headers(token: str) -> dict[str, str]:
    """Headers required for every authenticated bot endpoint."""
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
        "X-WECHAT-UIN": _random_uin(),
    }


# ─── QR login flow ───────────────────────────────────────
async def start_qr_login(*, channel: Channel) -> dict[str, Any]:
    """Ask iLink for a fresh login QR — no auth needed (it's pre-bind).

    The shape we return matches the ``WeChatQrDialog`` frontend
    contract: ``qr_id`` for subsequent polls, ``qrcode_image_data`` as
    the **image URL** from iLink (use as ``<img src>``), and a generous
    TTL. No server-side image fetch or re-encoding.
    """
    url = f"{_BASE}/ilink/bot/get_bot_qrcode"
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            resp = await cli.get(url, params={"bot_type": 3})
        data = resp.json() if resp.content else {}
    except httpx.HTTPError as e:
        log.exception("wechat ilink get_bot_qrcode failed: %s", e)
        return {
            "qr_id": "",
            "qrcode_image_data": "",
            "expires_in": _QR_TTL_SEC,
            "status": "error",
            "error": str(e),
        }

    if resp.status_code >= 400:
        log.warning("wechat ilink get_bot_qrcode HTTP %s: %s", resp.status_code, data)
        return {
            "qr_id": "",
            "qrcode_image_data": "",
            "expires_in": _QR_TTL_SEC,
            "status": "error",
            "error": f"upstream_http_{resp.status_code}",
        }

    qr_id = (
        data.get("qrcode") or data.get("qrcode_id") or (data.get("data") or {}).get("qrcode") or ""
    )
    img_url = str(
        data.get("qrcode_img_content") or (data.get("data") or {}).get("qrcode_img_content") or ""
    ).strip()
    if not qr_id or not img_url:
        return {
            "qr_id": qr_id,
            "qrcode_image_data": img_url,
            "expires_in": _QR_TTL_SEC,
            "status": "error",
            "error": "ilink_unexpected_response",
        }
    return {
        "qr_id": qr_id,
        "qrcode_image_data": img_url,
        "expires_in": _QR_TTL_SEC,
        "status": "pending",
    }


async def poll_qr_login(*, channel: Channel, qr_id: str) -> dict[str, Any]:
    """Poll the QR status. Returns ``{status, bot_token?, ...}``.

    Statuses:
        ``pending``    — waiting for scan (also covers transient upstream
                         hiccups — see below)
        ``scanned``    — QR scanned, waiting for confirm
        ``confirmed``  — token issued; route writes it back to ``config_json``
        ``expired``    — QR timed out, frontend should regen
        ``error``      — iLink explicitly rejected the QR session

    Transient policy: network timeouts, connection resets, and non-2xx
    upstream responses are mapped to ``pending`` so the dialog keeps
    polling. The frontend owns a 120 s local TTL timer that flips the
    dialog to ``expired`` if no positive result arrives — this gives
    the operator the friendly "refresh" path instead of an opaque
    "upstream_http_500" error that the i18n catalog can't localize.
    """
    url = f"{_BASE}/ilink/bot/get_qrcode_status"
    try:
        async with httpx.AsyncClient(timeout=20.0) as cli:
            resp = await cli.get(
                url,
                params={"qrcode": qr_id},
                headers={"iLink-App-ClientVersion": "1"},
            )
        data = resp.json() if resp.content else {}
    except httpx.HTTPError as e:
        log.warning("wechat ilink get_qrcode_status transport error: %s", e)
        return {"status": "pending"}

    if resp.status_code >= 400:
        log.warning(
            "wechat ilink get_qrcode_status HTTP %s: %s",
            resp.status_code,
            (resp.text or "")[:200],
        )
        return {"status": "pending"}

    body = data.get("data") if isinstance(data.get("data"), dict) else data
    status = body.get("status") or data.get("status") or ""
    if isinstance(status, str):
        status = status.strip().lower()

    if status == "confirmed":
        bot_token = body.get("bot_token") or body.get("access_token") or ""
        return {
            "status": "confirmed",
            "bot_token": bot_token,
            "ilink_user_id": body.get("ilink_user_id") or "",
            "ilink_bot_id": body.get("ilink_bot_id") or "",
            "baseurl": body.get("baseurl") or _BASE,
        }
    if status == "expired":
        return {"status": "expired"}
    if status == "scanned":
        return {"status": "scanned"}
    # iLink uses "wait" for the pre-scan idle state; we normalise to
    # the frontend's ``pending`` so the dialog speaks one vocabulary.
    if status in {"wait", "pending", ""}:
        return {"status": "pending"}
    # Unrecognised statuses (iLink occasionally adds transient values
    # such as "loading" / "processing") are *not* treated as fatal —
    # the QR remains scannable, so we keep the frontend polling rather
    # than slamming the dialog into a dead-end error state. The
    # original string is logged for diagnostics so genuinely broken
    # responses still surface in operator logs.
    log.debug(
        "wechat poll: unrecognised status %r — treating as pending",
        status,
    )
    return {"status": "pending"}


# ─── Long-poll loop ──────────────────────────────────────
async def run_long_poll(
    *,
    channel: Channel,
    dispatch: InboundDispatch,
    stop: asyncio.Event,
) -> None:
    """Long-poll iLink for new messages and route them through dispatch.

    Returns only when ``stop`` is set. Re-raises after recoverable
    errors so :class:`ChannelRuntime` applies its backoff schedule.
    """
    plain_cfg = getattr(channel, "_plain_config", None) or (channel.config_json or {})
    bot_token = str(plain_cfg.get("bot_token") or "").strip()
    base_url = str(plain_cfg.get("baseurl") or _BASE).strip().rstrip("/")
    bot_self_id = str(plain_cfg.get("ilink_bot_id") or "").strip()
    if not bot_token:
        log.info(
            "wechat channel %s missing bot_token — staying idle until QR-login",
            channel.id,
        )
        # Block on stop instead of busy-looping — once the operator scans,
        # the runtime restarts us with the new token.
        await stop.wait()
        return

    cursor = ""
    url = f"{base_url}/ilink/bot/getupdates"
    log.info(
        "wechat channel %s long-poll starting (base=%s bot_self=%r)",
        channel.id,
        base_url,
        bot_self_id or "<unknown>",
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(_LONG_POLL_TIMEOUT + 15)) as cli:
        round_no = 0
        while not stop.is_set():
            payload: dict[str, Any] = {
                "get_updates_buf": cursor,
                "base_info": {"channel_version": _CHANNEL_VERSION},
            }

            try:
                resp = await cli.post(url, json=payload, headers=_build_auth_headers(bot_token))
            except httpx.HTTPError as e:
                # Surface so runtime backs off; the close-then-reopen
                # behaviour matches what iLink expects when its edge
                # rotates.
                raise RuntimeError(f"wechat long-poll http error: {e}") from e

            data = resp.json() if resp.content else {}
            ret = data.get("ret", 0) or 0
            errcode = data.get("errcode", 0) or 0
            if ret == -14 or errcode == -14:
                raise ChannelStreamAuthExpired(
                    "wechat session expired (errcode=-14); operator must "
                    "re-scan the QR code to refresh the bot_token"
                )
            if (ret not in (0, None)) or (errcode not in (0, None)):
                raise RuntimeError(
                    f"wechat long-poll failed: ret={ret} errcode={errcode} "
                    f"errmsg={data.get('errmsg')!r}"
                )

            new_cursor = str(data.get("get_updates_buf") or "")
            if new_cursor and new_cursor != cursor:
                cursor = new_cursor

            messages = data.get("msgs") or []
            round_no += 1
            if messages:
                log.info(
                    "wechat channel %s round %d: %d msg(s) received",
                    channel.id,
                    round_no,
                    len(messages),
                )
            elif round_no % 20 == 0:
                # Heartbeat every ~20 idle rounds so log readers can see
                # the loop is alive without spamming each empty poll.
                log.debug(
                    "wechat channel %s long-poll idle (round=%d cursor=%r)",
                    channel.id,
                    round_no,
                    cursor[:16],
                )

            for msg in messages:
                from_id = str(
                    msg.get("from_user_id") or msg.get("from_user") or msg.get("from") or ""
                ).strip()
                # iLink echoes the bot's own outbound sends back through
                # ``getupdates`` — agent-replies hitting our own dispatch
                # would loop forever. Drop them before parsing.
                if bot_self_id and from_id == bot_self_id:
                    log.debug(
                        "wechat channel %s skipping self-echo from %r",
                        channel.id,
                        from_id,
                    )
                    continue
                inbound = _parse_ilink_message(msg)
                if inbound is None:
                    log.debug(
                        "wechat channel %s msg from %r had no text payload — skipped",
                        channel.id,
                        from_id,
                    )
                    continue
                # Drop messages we've already dispatched in this process.
                # iLink retransmits on cursor-loss; without dedup the agent
                # would run twice and the user would see a duplicate reply.
                msg_id = inbound.raw.get("msg_id") if inbound.raw else None
                if msg_id is not None:
                    seen = _PROCESSED_MSG_IDS.setdefault(
                        channel.id, _deque(maxlen=_PROCESSED_MSG_DEDUP_WINDOW)
                    )
                    if msg_id in seen:
                        log.info(
                            "wechat channel %s dropping retransmit msg_id=%s",
                            channel.id,
                            msg_id,
                        )
                        continue
                    seen.append(msg_id)
                log.info(
                    "wechat channel %s inbound from=%r preview=%r",
                    channel.id,
                    inbound.external_user,
                    inbound.user_text[:80],
                )
                await dispatch(inbound)

            if not messages:
                # No payload — let the loop breathe so we don't hot-spin
                # the API. Real long-polls block server-side already.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=_LONG_POLL_INTERVAL)


def _parse_ilink_message(msg: dict[str, Any]) -> InboundMessage | None:
    """Pull the user's text out of an iLink update entry.

    iLink's update shape:

        {"item_list": [{"type": 1, "text_item": {"text": "..."}, ...}]}

    where ``type=1`` is plain text. Anything else (image / file / event)
    is silently ignored — the agent has no way to render it back yet.

    The outbound ``sendmessage`` flow needs the inbound's
    ``context_token`` echoed back, so we stash it (and ``session_id``)
    in ``raw`` for ``post_reply`` to consume.
    """
    items = msg.get("item_list") or []
    text_parts: list[str] = []
    for item in items:
        if item.get("type") == 1:
            t = ((item.get("text_item") or {}).get("text") or "").strip()
            if t:
                text_parts.append(t)
    if not text_parts:
        return None

    text = "\n".join(text_parts).strip()
    from_user = msg.get("from_user_id") or msg.get("from_user") or msg.get("from") or ""
    if not from_user:
        return None
    context_token = str(msg.get("context_token") or "").strip()
    session_id = str(msg.get("session_id") or from_user).strip()
    # ``context_token`` MUST round-trip into the outbound reply (iLink
    # uses it to identify the conversation slot). Pack it into the
    # thread key so the dispatcher's ``send_text`` contract (which only
    # carries ``thread_key``) can extract it on the way back out.
    return InboundMessage(
        thread_key=f"wechat:{from_user}:{context_token}:{session_id}",
        user_text=text,
        external_user=str(from_user),
        raw={
            "msg_id": msg.get("msg_id"),
            "from_user_id": str(from_user),
            "session_id": session_id,
            "context_token": context_token,
        },
    )


_WECHAT_TEXT_LIMIT = 2000


def _split_wechat_text(text: str, limit: int = _WECHAT_TEXT_LIMIT) -> list[str]:
    """Split text along sane boundaries to obey the 2000-char rule."""
    remaining = text or ""
    chunks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        segment = remaining[:limit]
        cut = max(segment.rfind("\n\n"), segment.rfind("\n"), segment.rfind(" "))
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks or [""]


# ─── Typing indicator ────────────────────────────────────
async def fetch_typing_ticket(*, bot_token: str, base_url: str, ilink_user_id: str) -> str | None:
    """Return a usable typing ticket for ``bot_token``.

    iLink exposes the indicator behind two calls: ``getconfig`` returns a
    ``typing_ticket`` that any subsequent ``sendtyping`` reuses. Tickets
    are scoped per bot, not per user, and the server treats them as
    opaque for ~24h. We cache the result so high-traffic bots don't pay
    one ``getconfig`` per turn, and serialize concurrent misses behind a
    lock so a burst of inbound messages only triggers one fetch.

    ``ilink_user_id`` is required by the server (``ret=-2 ilink_user_id
    required`` when missing) and is the same value the QR-login flow
    wrote back into ``config_json``.

    Returns ``None`` on any error — the caller falls back to the legacy
    placeholder-text path so a failed indicator never blocks the agent
    turn itself.
    """
    if not bot_token or not ilink_user_id:
        return None
    now = time.time()
    cached = _TYPING_TICKET_CACHE.get(bot_token)
    if cached and cached[1] > now:
        return cached[0]

    async with _TYPING_TICKET_LOCK:
        cached = _TYPING_TICKET_CACHE.get(bot_token)
        if cached and cached[1] > now:
            return cached[0]
        base = (base_url or _BASE).rstrip("/")
        try:
            cli = await _get_outbound_client()
            resp = await cli.post(
                f"{base}/ilink/bot/getconfig",
                json={
                    "ilink_user_id": ilink_user_id,
                    "base_info": {"channel_version": _CHANNEL_VERSION},
                },
                headers=_build_auth_headers(bot_token),
                timeout=10.0,
            )
        except httpx.HTTPError as e:
            log.debug("wechat getconfig failed: %s", e)
            return None
        if resp.status_code >= 400:
            log.debug("wechat getconfig HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json() if resp.content else {}
        body = data.get("data") if isinstance(data.get("data"), dict) else data
        ticket = str(body.get("typing_ticket") or "").strip()
        if not ticket:
            return None
        _TYPING_TICKET_CACHE[bot_token] = (ticket, now + _TYPING_TICKET_TTL_SEC)
        return ticket


def _invalidate_typing_ticket(bot_token: str) -> None:
    _TYPING_TICKET_CACHE.pop(bot_token, None)


async def send_typing_status(
    *,
    bot_token: str,
    base_url: str,
    ilink_user_id: str,
    to_user_id: str,
    typing_ticket: str,
    status: int,
) -> None:
    """Toggle the "对方正在输入中" indicator for ``to_user_id``.

    ``status=1`` lights it up; ``status=2`` clears it. iLink expects a
    fresh ping every few seconds to keep the indicator alive, so callers
    drive a loop and rely on :func:`asyncio.shield` to land the stop
    signal even when the loop is cancelled.

    Raises ``RuntimeError`` on HTTP/transport failure so the caller can
    drop the indicator after one strike rather than spamming a dead
    endpoint.
    """
    base = (base_url or _BASE).rstrip("/")
    body = {
        "ilink_user_id": ilink_user_id,
        "to_user_id": to_user_id,
        "typing_ticket": typing_ticket,
        "status": int(status),
        "base_info": {"channel_version": _CHANNEL_VERSION},
    }
    cli = await _get_outbound_client()
    resp = await cli.post(
        f"{base}/ilink/bot/sendtyping",
        json=body,
        headers=_build_auth_headers(bot_token),
        timeout=10.0,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"wechat sendtyping HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json() if resp.content else {}
    ret = data.get("ret", 0) or 0
    errcode = data.get("errcode", 0) or 0
    if ret == -14 or errcode == -14:
        # Ticket / session retired by the server — drop the cached value
        # so the next call refetches instead of looping on a dead token.
        _invalidate_typing_ticket(bot_token)
        raise RuntimeError("wechat sendtyping session expired (errcode=-14)")
    if (ret not in (0, None)) or (errcode not in (0, None)):
        raise RuntimeError(
            f"wechat sendtyping failed: ret={ret} errcode={errcode} errmsg={data.get('errmsg')!r}"
        )


async def run_typing_keepalive(
    *,
    bot_token: str,
    base_url: str,
    ilink_user_id: str,
    to_user_id: str,
    typing_ticket: str,
) -> None:
    """Hold the typing indicator up until the surrounding task is cancelled.

    Loops a ``status=1`` ping every :data:`_TYPING_KEEPALIVE_SEC` seconds
    (just under iLink's idle decay) and shields a single ``status=2`` send
    in the cancel path so the indicator clears the moment the agent's
    real reply lands. Any non-cancel failure in the ping path stops the
    loop deterministically — a flapping indicator is worse than no
    indicator and the agent still finishes normally.
    """
    try:
        while True:
            try:
                await send_typing_status(
                    bot_token=bot_token,
                    base_url=base_url,
                    ilink_user_id=ilink_user_id,
                    to_user_id=to_user_id,
                    typing_ticket=typing_ticket,
                    status=1,
                )
            except Exception as e:
                log.debug("wechat typing keepalive aborted: %s", e)
                return
            await asyncio.sleep(_TYPING_KEEPALIVE_SEC)
    finally:
        with contextlib.suppress(Exception):
            await asyncio.shield(
                send_typing_status(
                    bot_token=bot_token,
                    base_url=base_url,
                    ilink_user_id=ilink_user_id,
                    to_user_id=to_user_id,
                    typing_ticket=typing_ticket,
                    status=2,
                )
            )


# ─── Outbound reply ──────────────────────────────────────
async def send_reply(
    *,
    bot_token: str,
    base_url: str,
    to_user_id: str,
    context_token: str,
    text: str,
) -> None:
    """Send one or more outbound text messages via ``sendmessage``."""
    if not (bot_token and to_user_id and context_token and (text or "").strip()):
        return
    base = (base_url or _BASE).rstrip("/")
    cli = await _get_outbound_client()
    for chunk in _split_wechat_text(text):
        body = {
            "msg": {
                "from_user_id": "",
                "to_user_id": to_user_id,
                "client_id": (
                    f"senharness-wechat:{int(time.time() * 1000)}-{uuid_pkg.uuid4().hex[:8]}"
                ),
                "message_type": 2,
                "message_state": 2,
                "context_token": context_token,
                "item_list": [
                    {
                        "type": 1,
                        "text_item": {"text": chunk},
                    }
                ],
            },
            "base_info": {"channel_version": _CHANNEL_VERSION},
        }
        resp = await cli.post(
            f"{base}/ilink/bot/sendmessage",
            json=body,
            headers=_build_auth_headers(bot_token),
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"wechat sendmessage HTTP {resp.status_code}: {resp.text[:300]}")
