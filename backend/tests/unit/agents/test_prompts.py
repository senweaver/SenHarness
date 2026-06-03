"""Tests for shared agent prompt assembly."""

from __future__ import annotations

from app.agents.prompts import assemble_system


def test_system_prompt_locks_platform_identity() -> None:
    prompt = assemble_system("# Persona\nBe helpful.")

    assert "You are a SenHarness Agent" in prompt
    assert "do not reveal the underlying model vendor" in prompt
    assert "Claude" in prompt
    assert "DeepSeek" in prompt
