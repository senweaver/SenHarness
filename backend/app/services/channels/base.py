"""Channel provider abstractions.

Each provider (Slack / Feishu / Discord / DingTalk / WeCom / generic
webhook) implements:

* ``parse_inbound(payload, headers) -> InboundMessage | None`` — extract the
  user text + thread key from the provider's webhook payload. Returns ``None``
  to signal "this is a control event, ignore it" (e.g. Slack ``url_verification``
  handshake, Feishu challenge, Discord PING) — the ingress route then returns
  the provider-specific handshake response instead of creating a session.

* ``handshake_response(payload) -> dict | None`` — optional. If the provider
  needs a specific HTTP reply for a control event, return it here; the
  ingress route will forward it verbatim.

* ``post_reply(channel, thread_key, text) -> None`` — post the agent's answer
  back to the provider's API. May be a no-op (generic webhook).

* ``metadata()`` (classmethod) — used by ``GET /api/v1/channels/kinds`` to
  populate the Channel-create form's provider picker. Defaults to a minimal
  descriptor; providers can override for richer UI.

* ``run_stream(channel, dispatch, stop)`` — optional. Streaming providers
  open a long-lived connection (WebSocket / Gateway / long-poll) and call
  ``dispatch(InboundMessage)`` for every received message. The runtime
  manages reconnect; the provider should never return until ``stop`` is set.

* ``send_text(channel_config, thread_key, text)`` — optional fast-path for
  outbound replies in stream mode. Defaults to ``post_reply``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

if TYPE_CHECKING:
    from app.db.models.channel import Channel


@dataclass
class InboundMessage:
    """Normalized inbound message from an IM channel."""

    # Stable key for the remote conversation thread (e.g. Slack ``channel_id``
    # + ``thread_ts``). Used to match subsequent messages to the same Session.
    thread_key: str
    user_text: str
    # External user identifier — human display name or email.
    external_user: str = "unknown"
    # Raw payload preserved on the Session row for debugging.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutboundButton:
    """One quick-reply button. ``value`` is what the user "says" on tap —
    a menu number, so a click is equivalent to replying that number.
    """

    label: str
    value: str


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """A presenter-rendered outbound reply (P1 rich channels).

    The presenter (``app.services.channels._presenter``) owns all the
    rendering logic; providers just send. ``text`` is the always-present
    plain-text rendering (the universal fallback). ``buttons`` is an
    optional agent-menu quick-reply set for ``supports_buttons`` channels.
    ``identity`` is an optional per-message bot identity
    (``{"name": ..., "team": ...}``) for ``per_message_identity`` channels
    when ``reply_attribution=identity``.
    """

    text: str
    buttons: tuple[OutboundButton, ...] | None = None
    identity: dict[str, str] | None = None


# Async callable that the runtime hands to ``run_stream``: when a streaming
# provider receives a real message it ``await dispatch(inbound)`` to push it
# into the same session/agent path the webhook ingress uses.
InboundDispatch = Callable[[InboundMessage], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class ChannelProviderMeta:
    """UI-facing descriptor for a channel provider.

    Drives ``GET /api/v1/channels/kinds`` and the Channel-create form's
    provider picker. Everything here is human-oriented metadata — the
    functional contract is the :class:`ChannelProvider` methods.
    """

    kind: str
    display_name: str
    description: str
    docs_url: str = ""
    # Names of the config_json keys an operator must fill in. The
    # frontend renders one input per entry; sensitive ones (names
    # containing "secret" / "token" / "password") get masked by the
    # Vault UI.
    required_config_fields: tuple[str, ...] = ()
    # Optional config keys — typed hints for the form (we still accept
    # any JSON).
    optional_config_fields: tuple[str, ...] = ()
    # True if the provider supports outbound reply (most do; the
    # generic webhook kind does not).
    supports_outbound: bool = True
    # Modes the provider supports. ``webhook`` means the provider accepts
    # inbound HTTPS pushes; ``stream`` means it dials out and holds a
    # long-lived connection. Most providers support exactly one; Feishu
    # / DingTalk / WeCom / Discord / QQ / WeChat-iLink support both.
    supported_modes: tuple[Literal["webhook", "stream"], ...] = ("webhook",)
    default_mode: Literal["webhook", "stream"] = "webhook"
    # Optional pip extra needed to enable stream mode. The frontend reads
    # this to render a clear "pip install '.[channels-stream]'" hint when
    # the SDK is missing.
    stream_requires_extra: str | None = None
    # Per-mode field overrides. When the same provider asks for very
    # different inputs depending on transport (e.g. DingTalk Stream takes
    # ``client_id`` + ``client_secret`` while DingTalk Webhook takes
    # ``webhook_url`` + ``sign_secret``), the create form would otherwise
    # have to show every field on every screen. These three keys let a
    # provider declare ``{mode: tuple[str, ...]}`` so the frontend renders
    # only what the active mode actually needs. Leaving them ``None``
    # means "fall back to ``required_config_fields`` /
    # ``optional_config_fields`` for every mode" — preserves the contract
    # for community adapters that don't bother with mode splits.
    mode_required_fields: dict[str, tuple[str, ...]] | None = None
    mode_optional_fields: dict[str, tuple[str, ...]] | None = None
    # Fields declared in the global ``required_/optional_config_fields``
    # but that should be hidden entirely under a given mode. Useful when
    # the provider keeps a field reachable for back-compat but it's only
    # meaningful in one mode.
    mode_hidden_fields: dict[str, tuple[str, ...]] | None = None


class SignatureInvalid(Exception):
    """Raised by :meth:`ChannelProvider.verify_signature` when a webhook
    request can't be authenticated. Ingress translates this into HTTP 403.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ChannelStreamAuthExpired(RuntimeError):
    """Raised when a streaming provider's long-lived credentials have
    expired (e.g. WeChat iLink ``errcode=-14``, an OAuth refresh token
    revoked at the source). The runtime treats this as a "needs operator
    re-auth" signal: it backs off far longer than a transient network
    error, and logs it once at INFO rather than spamming WARNING every
    cycle. Operator re-binds (QR scan / config edit) clear the state
    and let the loop resume normally.
    """


class ChannelProvider:
    """Base class. Subclasses must override at least :meth:`parse_inbound`.

    Subclasses should also set ``kind`` and override :meth:`metadata` to
    teach the frontend about their required config fields.
    """

    kind: ClassVar[str] = "base"

    @classmethod
    def metadata(cls) -> ChannelProviderMeta:
        """Return the UI descriptor for this provider.

        Default is a bare-bones shape; production providers override
        this to fill in display_name + description + config hints.
        Keeps the interface forgiving for community adapters while
        giving bundled providers full UI support.
        """
        return ChannelProviderMeta(
            kind=cls.kind,
            display_name=cls.kind.replace("_", " ").title(),
            description="",
        )

    @classmethod
    def supports_stream(cls) -> bool:
        """True iff this provider can run in pull/stream mode.

        The default is ``False`` — most webhook providers (Slack, Teams,
        Telegram, generic webhook) intentionally don't bother. Providers
        that override return ``True`` only when their stream SDK is
        actually importable; missing-SDK paths must downgrade gracefully
        so the rest of the registry keeps booting.
        """
        return False

    @classmethod
    def stream_available(cls) -> bool:
        """Realtime probe: can this process actually open a stream right now?

        Defaults to :meth:`supports_stream`. Subclasses with optional SDK
        deps override to ``False`` when the import fails. The frontend
        reads this through ``describe_providers`` and greys out the Mode
        toggle if the operator hasn't installed the extra.
        """
        return cls.supports_stream()

    def parse_inbound(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> InboundMessage | None:
        raise NotImplementedError

    def handshake_response(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return None

    def verify_signature(
        self,
        *,
        channel_config: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        """Authenticate an inbound webhook.

        Default: trust the ``?token=`` query string (already validated by the
        ingress route) and allow everything through. Providers that ship their
        own request signing (Slack v0 HMAC, Discord ed25519, Feishu
        verification token) override this and raise ``SignatureInvalid`` on
        mismatch. The ingress translates the exception into HTTP 403.

        Skipping signature check is allowed when the channel config explicitly
        opts out via ``{"verify_signatures": false}`` — useful for dev tunnels
        where headers are rewritten by the proxy.
        """
        return None

    async def post_reply(
        self, *, channel_config: dict[str, Any], thread_key: str, text: str
    ) -> None:
        """Default: no outbound reply (webhook-only)."""
        return None

    async def send_text(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        """Outbound text reply.

        Stream-mode providers usually have an active session/connection
        and can ship the message immediately; webhook-mode ones go
        through the standard REST endpoint. The default falls back to
        :meth:`post_reply` so legacy providers keep working without
        change. Override when you need to plumb through a held-open
        client/connection (Discord client, lark-oapi WebSocket, etc.).
        """
        await self.post_reply(channel_config=channel_config, thread_key=thread_key, text=text)

    async def send_message(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        message: OutboundMessage,
    ) -> None:
        """Send a presenter-rendered :class:`OutboundMessage`.

        The default ignores ``buttons`` / ``identity`` and ships the plain
        ``text`` via :meth:`send_text`, so every provider (and webhook-only
        ones) degrades gracefully. Rich providers (Slack / Telegram /
        Feishu) override this to render quick-reply buttons and per-message
        bot identity. Keep provider send code thin — the *what to render*
        decision lives in the presenter.
        """
        await self.send_text(
            channel_config=channel_config, thread_key=thread_key, text=message.text
        )

    async def send_processing_indicator(
        self,
        *,
        channel_config: dict[str, Any],
        thread_key: str,
        text: str,
    ) -> None:
        """Best-effort "agent is thinking" nudge. Default: no-op.

        Providers whose platform offers no native typing surface
        (WeChat iLink / WeCom) override this to post a short placeholder
        message so the end user sees activity while the agent works.
        Failures must not propagate — the indicator is purely cosmetic
        and should never block the real reply path.
        """
        return None

    async def run_stream(
        self,
        *,
        channel: Channel,
        dispatch: InboundDispatch,
        stop: asyncio.Event,
    ) -> None:
        """Open a streaming connection and dispatch inbound messages.

        Implementations must:
            * Block until ``stop.is_set()`` returns ``True``.
            * Re-raise on unrecoverable errors so the runtime applies
              the configured exponential reconnect backoff. Transient
              errors should be handled internally without throwing.
            * Call ``await dispatch(inbound)`` for every real message;
              control frames / heartbeats should be silently consumed.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement run_stream")
