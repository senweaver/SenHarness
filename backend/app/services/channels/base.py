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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar


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


class SignatureInvalid(Exception):
    """Raised by :meth:`ChannelProvider.verify_signature` when a webhook
    request can't be authenticated. Ingress translates this into HTTP 403.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


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
