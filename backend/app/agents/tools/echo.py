"""Smoke-test `echo` tool — used by Phase 0 to validate the kernel → tool pipe."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EchoArgs(BaseModel):
    text: str = Field(..., description="Text to echo back verbatim.")


def run_echo(args: EchoArgs) -> dict:
    return {"ok": True, "echo": args.text}
