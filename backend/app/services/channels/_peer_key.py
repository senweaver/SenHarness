"""Provider-neutral conversation key derivation (P0 routing).

``peer_key`` identifies "the conversation" so per-conversation routing
stickiness (``channel_conversation_state``) is keyed consistently across
providers:

* WeChat / WeCom — personal bots only ever see private chats, so the peer
  is the sender (``from_user``). One peer_key per end user.
* Feishu / Lark / Slack / Discord / DingTalk / Telegram — keyed by the
  group/thread ``chat_id`` so every sender in a group shares one sticky
  route (P0 group stickiness; per-sender override is P1).

``sender_key`` is always the individual external user id — used for
identity resolution (``channel_user_link``) and the sender allowlist —
even in a group where ``peer_key`` is the shared chat.

Group vs DM classification is conservative: we only call a conversation a
group when the provider positively says so (``raw.chat_type``). Personal
bots (WeChat) and providers that don't surface a chat type default to DM,
so the ``group_policy`` gate never accidentally blocks a 1:1 chat.
"""

from __future__ import annotations

from app.services.channels.base import InboundMessage

_PRIVATE_ONLY_KINDS = frozenset({"wechat", "wecom"})
_GROUP_CHAT_TYPES = frozenset({"group", "supergroup", "channel", "team"})
_DIRECT_CHAT_TYPES = frozenset({"p2p", "private", "direct", "single", "dm"})


def derive_sender_key(inbound: InboundMessage) -> str:
    """The individual external user id (for identity + allowlist)."""
    raw = inbound.raw or {}
    candidate = (
        raw.get("from_user_id") or raw.get("from_user") or inbound.external_user or "anonymous"
    )
    return str(candidate).strip() or "anonymous"


def derive_peer_key(*, kind: str, inbound: InboundMessage) -> str:
    """Stable conversation key for routing stickiness.

    See module docstring for the per-provider semantics.
    """
    raw = inbound.raw or {}
    if kind in _PRIVATE_ONLY_KINDS:
        return derive_sender_key(inbound)

    chat_id = raw.get("chat_id")
    if chat_id:
        return str(chat_id).strip()

    # Slack / Discord / generic providers pack the conversation into the
    # ``thread_key`` as ``"<kind>:<conversation>:..."``. Use the middle
    # segment when present so a thread maps to one route.
    parts = (inbound.thread_key or "").split(":")
    if len(parts) >= 2 and parts[1].strip():
        return parts[1].strip()
    return (inbound.thread_key or derive_sender_key(inbound)).strip()


def is_group_conversation(*, kind: str, inbound: InboundMessage) -> bool:
    """True only when the provider positively identifies a group chat."""
    if kind in _PRIVATE_ONLY_KINDS:
        return False
    raw = inbound.raw or {}
    chat_type = str(raw.get("chat_type") or "").strip().lower()
    if chat_type in _GROUP_CHAT_TYPES:
        return True
    if chat_type in _DIRECT_CHAT_TYPES:
        return False
    return False


__all__ = ["derive_peer_key", "derive_sender_key", "is_group_conversation"]
