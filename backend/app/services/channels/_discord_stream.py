"""Discord Gateway — uses the community ``discord.py`` SDK."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from app.services.channels.base import InboundDispatch, InboundMessage

if TYPE_CHECKING:
    from app.db.models.channel import Channel

log = logging.getLogger(__name__)

# Active client registry — keyed by channel id stringified — so
# ``send_text`` can reuse the held-open client without re-authing.
_ACTIVE_CLIENTS: dict[str, Any] = {}


async def run_gateway_stream(
    *,
    channel: Channel,
    dispatch: InboundDispatch,
    stop: asyncio.Event,
) -> None:
    try:
        import discord
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "discord.py extra missing; install with "
            "'pip install \".[channels-stream]\"'"
        ) from e

    plain = getattr(channel, "_plain_config", None) or (channel.config_json or {})
    bot_token = str(plain.get("bot_token") or "").strip()
    if not bot_token:
        log.info("discord channel %s missing bot_token — idle", channel.id)
        await stop.wait()
        return

    # M0.8 guild scoping. Empty ``allowed_guild_ids`` keeps the row
    # listening on every guild it is a member of (back-compat for
    # channels that pre-date the toggle). DMs are blocked by default
    # so a leaked bot token can't be exploited via a 1:1 chat.
    allowed_guild_ids: set[str] = {
        str(g).strip() for g in (plain.get("allowed_guild_ids") or []) if str(g).strip()
    }
    allow_dms: bool = bool(plain.get("allow_dms", False))

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guild_messages = True
    intents.dm_messages = True

    channel_id_str = str(channel.id)
    workspace_id = getattr(channel, "workspace_id", None)

    async def _audit_filtered(*, guild_id: str | None, reason: str) -> None:
        try:
            from app.db.session import get_session_factory
            from app.services import audit as audit_svc

            factory = get_session_factory()
            async with factory() as db:
                await audit_svc.record(
                    db,
                    action="channel.discord_filtered",
                    actor_identity_id=None,
                    workspace_id=workspace_id,
                    resource_type="channel",
                    resource_id=channel.id,
                    summary=f"discord message filtered ({reason})",
                    metadata={
                        "channel_id": channel_id_str,
                        "guild_id": guild_id,
                        "reason": reason,
                    },
                )
                await db.commit()
        except Exception:  # pragma: no cover
            log.exception("discord_filtered audit write failed")

    class _Bot(discord.Client):
        async def on_ready(self) -> None:  # type: ignore[override]
            log.info("discord client ready as %s", self.user)

        async def on_message(self, message: Any) -> None:  # type: ignore[override]
            if message.author.bot:
                return
            text = (message.content or "").strip()
            if not text:
                return

            guild = getattr(message, "guild", None)
            if guild is None:
                if not allow_dms:
                    await _audit_filtered(guild_id=None, reason="dm_blocked")
                    return
            elif allowed_guild_ids and str(getattr(guild, "id", "")) not in allowed_guild_ids:
                await _audit_filtered(
                    guild_id=str(getattr(guild, "id", "")),
                    reason="guild_not_in_allowlist",
                )
                return

            inbound = InboundMessage(
                thread_key=f"discord_gw:{message.channel.id}",
                user_text=text,
                external_user=str(message.author),
                raw={
                    "guild_id": getattr(message.guild, "id", None),
                    "channel_id": message.channel.id,
                    "message_id": message.id,
                },
            )
            await dispatch(inbound)

    client = _Bot(intents=intents)
    _ACTIVE_CLIENTS[str(channel.id)] = client
    run_task = asyncio.create_task(
        client.start(bot_token), name=f"discord-gw-{str(channel.id)[:8]}"
    )
    try:
        await stop.wait()
    finally:
        try:
            await client.close()
        except Exception:  # pragma: no cover
            log.exception("discord client.close failed")
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await run_task
        _ACTIVE_CLIENTS.pop(str(channel.id), None)
