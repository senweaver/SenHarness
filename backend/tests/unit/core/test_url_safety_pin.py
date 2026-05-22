"""Coverage for the M0.6 ``resolve_safe_url`` helper.

The original ``assert_safe_url`` tests live in
``test_url_safety.py`` and exercise the rejection matrix; this file
focuses on the new tuple return path and the DNS-rebinding pin.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.url_safety import UnsafeURLError, resolve_safe_url


class TestResolveSafeUrlReturnsPin:
    def test_dns_resolved_host_returns_pinned_ip(self):
        with patch(
            "app.core.url_safety._resolve_all",
            return_value=["93.184.216.34"],
        ):
            url, pinned = resolve_safe_url("https://example.com/x")
        assert url == "https://example.com/x"
        assert pinned == "93.184.216.34"

    def test_literal_ip_url_pin_is_none(self):
        url, pinned = resolve_safe_url("http://93.184.216.34/")
        assert url == "http://93.184.216.34/"
        assert pinned is None

    def test_first_resolved_ip_pinned(self):
        # Order matters — the first non-dangerous IP returned by DNS is
        # the pin we hand back to the caller.
        with patch(
            "app.core.url_safety._resolve_all",
            return_value=["93.184.216.34", "8.8.8.8"],
        ):
            _, pinned = resolve_safe_url("https://example.com/")
        assert pinned == "93.184.216.34"


class TestResolveSafeUrlRejectsSame:
    """Mirrors a representative subset of the rejection matrix to make sure
    the pinned variant doesn't accidentally weaken the SSRF guard."""

    @pytest.mark.parametrize(
        "url",
        ["file:///etc/passwd", "gopher://x/", "ftp://x/"],
    )
    def test_scheme_rejected(self, url):
        with pytest.raises(UnsafeURLError) as exc:
            resolve_safe_url(url)
        assert exc.value.code == "ssrf.scheme_blocked"

    def test_hostname_blocklist(self):
        with pytest.raises(UnsafeURLError) as exc:
            resolve_safe_url("http://localhost/secret")
        assert exc.value.code == "ssrf.blocked_hostname"

    def test_metadata_pinned(self):
        with (
            patch(
                "app.core.url_safety._resolve_all",
                return_value=["169.254.169.254"],
            ),
            pytest.raises(UnsafeURLError) as exc,
        ):
            resolve_safe_url("http://anything/")
        assert exc.value.code == "ssrf.metadata_endpoint"

    def test_private_pinned_rejects_by_default(self):
        with (
            patch(
                "app.core.url_safety._resolve_all",
                return_value=["10.0.0.1"],
            ),
            pytest.raises(UnsafeURLError) as exc,
        ):
            resolve_safe_url("http://corp.local/")
        assert exc.value.code == "ssrf.private_address"

    def test_allow_private_returns_pin(self):
        with patch(
            "app.core.url_safety._resolve_all",
            return_value=["10.0.0.1"],
        ):
            url, pinned = resolve_safe_url("http://corp.local/", allow_private=True)
        assert url == "http://corp.local/"
        assert pinned == "10.0.0.1"

    def test_dns_rebind_caught_pin_path(self):
        with (
            patch(
                "app.core.url_safety._resolve_all",
                return_value=["93.184.216.34", "127.0.0.1"],
            ),
            pytest.raises(UnsafeURLError) as exc,
        ):
            resolve_safe_url("http://dns-rebind.example/")
        assert exc.value.code == "ssrf.private_address"
