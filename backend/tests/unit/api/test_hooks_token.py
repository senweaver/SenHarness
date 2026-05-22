"""Webhook ingress token resolver tests.

The resolver is the single chokepoint between "accept token from HTTP
layer" and "authenticate the channel". Query-string tokens still work
for legacy compat but must emit a deprecation warning.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException

from app.api.helpers import resolve_ingress_token as _resolve_ingress_token


class TestTokenResolver:
    def test_header_preferred_over_query(self):
        # When both are present, header wins.
        assert _resolve_ingress_token("header-token", "query-token") == "header-token"

    def test_header_only(self):
        assert _resolve_ingress_token("header-token", None) == "header-token"

    def test_query_only_works_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING):
            assert _resolve_ingress_token(None, "query-token") == "query-token"
        assert any("query string" in record.message.lower() for record in caplog.records)

    def test_neither_is_401(self):
        with pytest.raises(HTTPException) as exc:
            _resolve_ingress_token(None, None)
        assert exc.value.status_code == 401
        detail = exc.value.detail
        assert isinstance(detail, dict)
        assert detail["code"] == "hooks.missing_token"

    def test_empty_string_treated_as_missing(self):
        """Zero-length strings shouldn't satisfy the resolver."""
        with pytest.raises(HTTPException):
            _resolve_ingress_token("", "")
