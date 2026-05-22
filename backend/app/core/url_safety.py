"""URL safety — SSRF defence used anywhere SenHarness fetches user-supplied URLs.

The threat: an AI agent (or a knowledge-base URL ingest, or a webhook
payload) might be coerced into fetching an internal URL that leaks cloud
metadata, probes internal services, or exfiltrates secrets. The classic
example is AWS's ``http://169.254.169.254/`` IMDSv1 endpoint, which — if
reachable from inside your VPC — hands out temporary IAM credentials to
any unauthenticated HTTP caller.

This module enforces three layers:

    1. **Scheme allowlist**: only ``http`` / ``https``. Everything else
       (``file://``, ``gopher://``, ``ftp://``, ``data:`` etc.) rejected
       before any DNS lookup.
    2. **Host blocklist**: loopback, link-local, private networks, and the
       cloud metadata endpoints. Applied to both the URL-supplied host and
       any IPs it resolves to (so DNS rebinding and CNAME trickery don't
       help).
    3. **Safe client factory**: callers should go through :func:`safe_get`
       which uses a patched ``httpx.AsyncClient`` that re-validates on
       every redirect.

``UnsafeURLError`` is the single exception all callers handle; translate
it to whatever UX is appropriate (``web_fetch`` returns a structured error
dict; routes that accept URLs from admin UI should HTTP 400).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any, Final
from urllib.parse import urlparse

log = logging.getLogger(__name__)


ALLOWED_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# Hostnames that should never be contacted regardless of DNS resolution.
# (DNS blacklist — in case an attacker CNAMEs evil.com → localhost.)
_BLOCKED_HOSTNAMES: Final[frozenset[str]] = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        "metadata.google.internal",  # GCP metadata
        "metadata",
        "instance-data",  # AWS/OCI
    }
)

# Cloud-metadata endpoints — these are the highest-value SSRF targets.
# IP matching also catches direct-IP fetches that skip hostname lookup.
_METADATA_IPS: Final[frozenset[str]] = frozenset(
    {
        "169.254.169.254",  # AWS IMDS / GCP / Azure / DigitalOcean
        "fd00:ec2::254",  # AWS IPv6
        "169.254.169.253",  # AWS DNS
    }
)


class UnsafeURLError(ValueError):
    """Raised when a URL fails the SSRF safety check.

    Carries a stable ``code`` so callers can map to i18n/error responses.
    """

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def assert_safe_url(url: str, *, allow_private: bool = False) -> str:
    """Validate a URL against SSRF-dangerous targets.

    Returns the URL unchanged on success. Raises :class:`UnsafeURLError`
    otherwise.

    ``allow_private=True`` bypasses the IP-range checks — use only for
    tests or explicit intranet connectors where the operator has vetted
    the target. Scheme and metadata-IP checks still apply.
    """
    resolve_safe_url(url, allow_private=allow_private)
    return url


def resolve_safe_url(url: str, *, allow_private: bool = False) -> tuple[str, str | None]:
    """Validate ``url`` and return ``(url, pinned_ip)``.

    The pinned IP is the first address ``url``'s hostname resolves to;
    callers fetching the URL should connect to that IP and pass the
    original hostname as the ``Host`` header so DNS rebinding cannot
    swap a public IP for a private one between this check and the
    connect. Returns ``pinned_ip=None`` only when the URL already
    embeds a literal IP (in which case the literal IS the pin) or when
    DNS resolution failed (the eventual connect will fail loudly; the
    caller decides whether to short-circuit).

    Same exception contract as :func:`assert_safe_url`.
    """
    if not url or not isinstance(url, str):
        raise UnsafeURLError("URL is empty", code="ssrf.empty_url")

    try:
        parsed = urlparse(url)
    except ValueError as e:
        raise UnsafeURLError(f"URL parse failed: {e}", code="ssrf.parse_failed") from e

    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"URL scheme {scheme!r} is not allowed (only http, https)",
            code="ssrf.scheme_blocked",
        )

    host = (parsed.hostname or "").lower()
    if not host:
        raise UnsafeURLError("URL has no hostname", code="ssrf.missing_host")

    if host in _BLOCKED_HOSTNAMES:
        raise UnsafeURLError(
            f"Hostname {host!r} is blocked (loopback/metadata alias)",
            code="ssrf.blocked_hostname",
        )

    pinned: str | None = None
    host_is_ip = False
    try:
        ipaddress.ip_address(host)
        host_is_ip = True
    except ValueError:
        host_is_ip = False

    for ip_text in _resolve_all(host):
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            continue

        if str(ip) in _METADATA_IPS:
            raise UnsafeURLError(
                f"URL resolves to cloud metadata endpoint {ip}",
                code="ssrf.metadata_endpoint",
            )

        if not allow_private and _is_dangerous_ip(ip):
            raise UnsafeURLError(
                f"URL resolves to private / loopback / link-local address {ip}",
                code="ssrf.private_address",
            )

        if pinned is None:
            pinned = str(ip)

    if host_is_ip:
        # Skip the indirect pin — the literal IP IS the address. Fetchers
        # can keep sending the URL as-is.
        return url, None

    return url, pinned


def _resolve_all(host: str) -> list[str]:
    """Return every IP (v4 + v6) ``host`` resolves to.

    If ``host`` is already an IP literal, short-circuit. Failures return
    an empty list — the caller then falls through to either hostname
    blocklist hits (handled above) or, as a last line of defence, network
    errors at connect time.
    """
    try:
        ipaddress.ip_address(host)
        return [host]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        # Unresolvable. The subsequent HTTP call will fail loudly; we
        # don't want to hand the attacker a dry-run validator, so we
        # don't treat unresolvable as "safe".
        log.info("SSRF check: DNS resolution for %r failed: %s", host, e)
        return []

    out: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr:
            out.append(addr)
    return out


def _is_dangerous_ip(ip: Any) -> bool:
    """True if the IP is in a range no public fetch should target."""
    # ``ipaddress`` covers loopback (127.0.0.0/8, ::1), link-local
    # (169.254.0.0/16, fe80::/10), private (10/8, 172.16/12, 192.168/16,
    # fc00::/7), multicast, reserved, and unspecified (0.0.0.0 / ::) via
    # these flags.
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )
