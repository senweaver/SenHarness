"""SSRF defence tests.

We assert on ``UnsafeURLError.code`` rather than message text so the
i18n-stable error keys don't silently drift.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.url_safety import UnsafeURLError, assert_safe_url


class TestSchemeAllowlist:
    def test_http_is_allowed(self):
        # Public host we control the mock for.
        with patch(
            "app.core.url_safety._resolve_all",
            return_value=["93.184.216.34"],  # example.com, public IP
        ):
            assert assert_safe_url("http://example.com/") == "http://example.com/"

    def test_https_is_allowed(self):
        with patch(
            "app.core.url_safety._resolve_all", return_value=["93.184.216.34"]
        ):
            assert assert_safe_url("https://example.com/x") == "https://example.com/x"

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "gopher://internal/",
            "ftp://files.example.com/",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
            "dict://localhost:11211/stat",
        ],
    )
    def test_non_http_schemes_rejected(self, url):
        with pytest.raises(UnsafeURLError) as exc:
            assert_safe_url(url)
        # Either scheme_blocked (file/gopher/...) or parse_failed
        # (javascript: resolves but has no host, parse_failed is fine too).
        assert exc.value.code in {
            "ssrf.scheme_blocked",
            "ssrf.missing_host",
        }


class TestBlockedHostnames:
    @pytest.mark.parametrize(
        "host",
        [
            "localhost",
            "LocalHost",
            "metadata.google.internal",
            "metadata",
            "instance-data",
        ],
    )
    def test_hostname_blocklist(self, host):
        with pytest.raises(UnsafeURLError) as exc:
            assert_safe_url(f"http://{host}/secret")
        assert exc.value.code == "ssrf.blocked_hostname"


class TestMetadataEndpoints:
    """169.254.169.254 is always blocked, even when ``allow_private=True``."""

    def test_aws_imds_blocked(self):
        with patch(
            "app.core.url_safety._resolve_all",
            return_value=["169.254.169.254"],
        ):
            with pytest.raises(UnsafeURLError) as exc:
                assert_safe_url("http://anything/")
        assert exc.value.code == "ssrf.metadata_endpoint"

    def test_direct_imds_ip_blocked(self):
        # Same check via direct-IP URL (no DNS resolution).
        with pytest.raises(UnsafeURLError) as exc:
            assert_safe_url("http://169.254.169.254/latest/meta-data/")
        assert exc.value.code == "ssrf.metadata_endpoint"

    def test_aws_ipv6_metadata_blocked(self):
        with patch(
            "app.core.url_safety._resolve_all", return_value=["fd00:ec2::254"]
        ):
            with pytest.raises(UnsafeURLError) as exc:
                assert_safe_url("http://example-aws.internal/")
        assert exc.value.code == "ssrf.metadata_endpoint"


class TestPrivateRanges:
    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.1.2.3",
            "10.0.0.5",
            "172.16.5.1",
            "172.31.255.255",
            "192.168.1.1",
            "169.254.100.100",  # link-local (non-metadata)
            "0.0.0.0",
            "::1",
            "fe80::1",
            "fc00::1",  # ULA
        ],
    )
    def test_private_ip_blocked(self, ip):
        with patch("app.core.url_safety._resolve_all", return_value=[ip]):
            with pytest.raises(UnsafeURLError) as exc:
                assert_safe_url(f"http://evil-cname.example/{ip}")
        assert exc.value.code in {
            "ssrf.private_address",
            "ssrf.metadata_endpoint",
        }

    def test_allow_private_bypasses_range_check(self):
        """``allow_private=True`` is for trusted intranet connectors that
        are explicitly wired up by the operator."""
        with patch(
            "app.core.url_safety._resolve_all", return_value=["10.0.0.5"]
        ):
            assert (
                assert_safe_url("http://intranet.local/", allow_private=True)
                == "http://intranet.local/"
            )

    def test_allow_private_still_blocks_metadata(self):
        """Cloud metadata endpoints have no legitimate use, so they stay
        blocked even with the allow_private escape hatch."""
        with patch(
            "app.core.url_safety._resolve_all", return_value=["169.254.169.254"]
        ):
            with pytest.raises(UnsafeURLError) as exc:
                assert_safe_url("http://anything/", allow_private=True)
        assert exc.value.code == "ssrf.metadata_endpoint"


class TestEdgeCases:
    def test_empty_url(self):
        with pytest.raises(UnsafeURLError) as exc:
            assert_safe_url("")
        assert exc.value.code == "ssrf.empty_url"

    def test_none_url(self):
        with pytest.raises(UnsafeURLError):
            assert_safe_url(None)  # type: ignore[arg-type]

    def test_dns_rebind_caught(self):
        """A hostname that resolves to both a public and a private IP
        must be rejected — the attacker could re-resolve post-check to
        hand us the private one."""
        with patch(
            "app.core.url_safety._resolve_all",
            return_value=["93.184.216.34", "127.0.0.1"],
        ):
            with pytest.raises(UnsafeURLError) as exc:
                assert_safe_url("http://dns-rebind.example/")
        assert exc.value.code == "ssrf.private_address"
