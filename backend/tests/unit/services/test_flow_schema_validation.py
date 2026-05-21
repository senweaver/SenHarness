"""Pure-function coverage for the M0.6 ``ScriptModeConfig`` /
``HttpModeConfig`` validators and ``FlowCreate`` cross-field check.

These run without DB / Redis so the rejection matrix is verifiable on a
dev laptop too.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.models.flow import FlowExecutionMode
from app.schemas.flow import (
    FlowCreate,
    HttpModeConfig,
    ScriptModeConfig,
)


class TestScriptModeConfig:
    def test_minimal_command_ok(self):
        cfg = ScriptModeConfig(script_command="echo hi")
        assert cfg.script_timeout_s == 60
        assert cfg.escalate_on_nonempty_output is True

    def test_empty_command_rejected(self):
        with pytest.raises(ValidationError):
            ScriptModeConfig(script_command="")

    def test_timeout_cap(self):
        with pytest.raises(ValidationError):
            ScriptModeConfig(script_command="x", script_timeout_s=10_000)

    @pytest.mark.parametrize("bad_key", ["A;B", "A`B", "A$B", "A\nB"])
    def test_env_key_blocks_shell_meta(self, bad_key):
        with pytest.raises(ValidationError):
            ScriptModeConfig(
                script_command="x",
                script_env={bad_key: "v"},
            )

    def test_escalate_flag_passthrough(self):
        cfg = ScriptModeConfig(
            script_command="x", escalate_on_nonempty_output=False
        )
        assert cfg.escalate_on_nonempty_output is False


class TestHttpModeConfig:
    def test_minimal_url_ok(self):
        cfg = HttpModeConfig(http_url="https://example.com/")
        assert cfg.http_method == "GET"
        assert cfg.escalate_on_http_failure is True

    def test_empty_url_rejected(self):
        with pytest.raises(ValidationError):
            HttpModeConfig(http_url="")

    @pytest.mark.parametrize(
        "bad_method",
        ["PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "CONNECT", "get"],
    )
    def test_method_allowlist(self, bad_method):
        with pytest.raises(ValidationError):
            HttpModeConfig(
                http_url="https://example.com/", http_method=bad_method
            )

    def test_body_only_for_post(self):
        with pytest.raises(ValidationError):
            HttpModeConfig(
                http_url="https://example.com/",
                http_method="GET",
                http_body="payload",
            )

    def test_post_body_ok(self):
        cfg = HttpModeConfig(
            http_url="https://example.com/",
            http_method="POST",
            http_body='{"x":1}',
        )
        assert cfg.http_body == '{"x":1}'

    def test_header_crlf_rejected(self):
        with pytest.raises(ValidationError):
            HttpModeConfig(
                http_url="https://example.com/",
                http_headers={"X-Bad": "value\r\nInjected: yes"},
            )

    def test_expected_status_range_check(self):
        with pytest.raises(ValidationError):
            HttpModeConfig(
                http_url="https://example.com/",
                http_expected_status=[42],
            )

    def test_expected_status_custom_set(self):
        cfg = HttpModeConfig(
            http_url="https://example.com/",
            http_expected_status=[200, 204],
        )
        assert cfg.http_expected_status == [200, 204]


class TestFlowCreateCrossField:
    def test_agent_mode_no_config_required(self):
        FlowCreate(name="x", agent_id=None, prompt_template="hi")

    def test_script_mode_missing_command_rejected(self):
        with pytest.raises(ValidationError):
            FlowCreate(
                name="x",
                execution_mode=FlowExecutionMode.NO_AGENT_SCRIPT,
                trigger_config={},
            )

    def test_script_mode_with_command_ok(self):
        FlowCreate(
            name="x",
            execution_mode=FlowExecutionMode.NO_AGENT_SCRIPT,
            trigger_config={"script_command": "echo hi"},
        )

    def test_http_mode_missing_url_rejected(self):
        with pytest.raises(ValidationError):
            FlowCreate(
                name="x",
                execution_mode=FlowExecutionMode.NO_AGENT_HTTP,
                trigger_config={},
            )

    def test_http_mode_with_url_ok(self):
        FlowCreate(
            name="x",
            execution_mode=FlowExecutionMode.NO_AGENT_HTTP,
            trigger_config={"http_url": "https://example.com/health"},
        )
