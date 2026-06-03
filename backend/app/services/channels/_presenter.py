"""Outbound presenter + channel-capability abstraction (P0 + P1).

Every routed reply leaves through one presenter so reply attribution
("who answered") and the agent switch menu are consistent across channels
while the *form* degrades to each channel's capabilities:

* Plain-text channels (WeChat iLink / WeCom / webhook): prefix the agent
  name — ``【客服小美】…``; team replies use ``【团队 › 成员】…``. The agent
  menu is a numbered list; a bare number selects.
* Rich channels (Slack / Discord / Feishu / Telegram / DingTalk / Teams):
  the menu also renders quick-reply **buttons** (tapping a button = replying
  that number). Channels that can change the sender's bot name/avatar per
  message (``per_message_identity``) carry the answering agent's identity
  out-of-band when ``reply_attribution=identity`` instead of the text prefix.

``menu_style`` chooses between the two: ``auto`` ⇒ buttons on rich channels +
numbers everywhere, ``text`` ⇒ numbers only, ``buttons`` ⇒ force buttons when
the channel supports them.

The agent display name flows in from the caller; the agent common-noun is the
workspace ``branding.agent_term`` and is handled by ``_copy`` — the presenter
only deals with concrete agent/team *names*. ``provider`` just sends; all the
"what to render" logic lives here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.services.channels import _copy
from app.services.channels.base import OutboundButton

# Per-provider capabilities: (supports_buttons, supports_cards,
# per_message_identity). Anything not listed is treated as plain text.
_CAPABILITIES: dict[str, tuple[bool, bool, bool]] = {
    "slack": (True, True, True),
    "discord": (True, True, True),
    "telegram": (True, False, False),
    "feishu": (True, True, False),
    "lark": (True, True, False),
    "dingtalk": (True, True, False),
    "teams": (True, True, False),
    "qq": (False, False, False),
    "wechat": (False, False, False),
    "wecom": (False, False, False),
    "webhook": (False, False, False),
}


@dataclass(frozen=True, slots=True)
class ChannelCapabilities:
    supports_buttons: bool
    supports_cards: bool
    per_message_identity: bool


@dataclass(frozen=True, slots=True)
class RenderedReply:
    """A presenter-rendered agent reply.

    ``text`` is always set. ``identity`` is non-None only on
    ``per_message_identity`` channels under ``reply_attribution=identity``;
    in that case the text carries no ``【name】`` prefix because the bot
    identity already attributes the reply.
    """

    text: str
    identity: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RenderedMenu:
    """A presenter-rendered agent menu — text plus optional quick-replies."""

    text: str
    buttons: tuple[OutboundButton, ...] | None = None


def capabilities_for(kind: str) -> ChannelCapabilities:
    buttons, cards, identity = _CAPABILITIES.get(kind, (False, False, False))
    return ChannelCapabilities(
        supports_buttons=buttons,
        supports_cards=cards,
        per_message_identity=identity,
    )


def attribution_prefix(*, agent_name: str, team_name: str | None) -> str:
    if team_name:
        return f"【{team_name} \u203a {agent_name}】"
    return f"【{agent_name}】"


def present_reply(
    *,
    kind: str,
    text: str,
    agent_name: str,
    team_name: str | None = None,
    attribution: str = "prefix",
    lang: str = _copy.DEFAULT_LANG,
    show_footer: bool = False,
) -> str:
    """Plain-text rendering of an agent reply (back-compat string API).

    Retained for callers that only need a string; new code should prefer
    :func:`render_reply`, which also surfaces the per-message identity for
    capable channels. This always returns the prefixed text (the universal
    fallback shape).
    """
    return render_reply(
        kind=kind,
        text=text,
        agent_name=agent_name,
        team_name=team_name,
        attribution=attribution,
        lang=lang,
        show_footer=show_footer,
        _force_prefix=True,
    ).text


def render_reply(
    *,
    kind: str,
    text: str,
    agent_name: str,
    team_name: str | None = None,
    attribution: str = "prefix",
    lang: str = _copy.DEFAULT_LANG,
    show_footer: bool = False,
    _force_prefix: bool = False,
) -> RenderedReply:
    """Render an agent reply with attribution + optional footer.

    * ``off`` — raw body, no attribution.
    * ``identity`` on a ``per_message_identity`` channel — body without a
      name prefix plus an ``identity`` payload the provider applies as the
      bot's display name (real per-message identity). Channels lacking the
      capability fall back to the name prefix.
    * ``prefix`` (and the identity fallback) — ``【name】body``.
    """
    body = text or ""
    caps = capabilities_for(kind)
    identity: dict[str, str] | None = None

    if attribution == "off":
        rendered = body
    elif attribution == "identity" and caps.per_message_identity and not _force_prefix:
        identity = {"name": agent_name}
        if team_name:
            identity["team"] = team_name
        rendered = body
    else:
        rendered = f"{attribution_prefix(agent_name=agent_name, team_name=team_name)}{body}"

    if show_footer:
        rendered = f"{rendered}\n\n{_copy.footer(lang=lang)}"
    return RenderedReply(text=rendered, identity=identity)


def render_menu(
    *,
    kind: str,
    menu_style: str,
    text: str,
    options: Sequence[tuple[int, str, str | None]],
) -> RenderedMenu:
    """Attach quick-reply buttons to an already-localized menu ``text``.

    ``options`` is the ordered ``[(index, name, desc)]`` the menu copy
    rendered. Buttons are produced only when the channel
    ``supports_buttons`` and ``menu_style`` permits (``auto`` / ``buttons``);
    ``text`` (the numbered list) is always present so a tap-less reply of
    the number still works and plain-text channels degrade cleanly.
    """
    caps = capabilities_for(kind)
    want_buttons = menu_style in ("auto", "buttons") and caps.supports_buttons
    if not want_buttons or not options:
        return RenderedMenu(text=text, buttons=None)
    buttons = tuple(
        OutboundButton(label=f"{idx}. {name}", value=str(idx)) for idx, name, _desc in options
    )
    return RenderedMenu(text=text, buttons=buttons)


__all__ = [
    "ChannelCapabilities",
    "RenderedMenu",
    "RenderedReply",
    "attribution_prefix",
    "capabilities_for",
    "present_reply",
    "render_menu",
    "render_reply",
]
