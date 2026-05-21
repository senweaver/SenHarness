"""Sender allowlist / denylist gate for channel inbound traffic.

Schema lives on ``Channel.sender_allowlist_json``::

    {
      "mode": "allow_all" | "allow_listed" | "deny_listed",
      "allow": ["external_user_id_1", ...],
      "deny":  ["external_user_id_1", ...]
    }

Default mode is ``allow_all`` so rows that pre-date M0.8 — and any new
row whose admin hasn't bothered configuring the gate — keep behaving
like before. Switching to ``allow_listed`` / ``deny_listed`` is an
explicit admin action; an unknown ``mode`` is treated as fail-open
plus an ``audit_events`` row so operators see they need to fix the
config without losing inbound traffic.
"""

from __future__ import annotations


def is_sender_allowed(rules: dict | None, external_user_id: str | None) -> bool:
    rules = rules or {}
    mode = rules.get("mode") or "allow_all"
    if mode == "allow_all":
        return True
    sender = (external_user_id or "").strip()
    if mode == "allow_listed":
        return sender in {str(s).strip() for s in (rules.get("allow") or []) if str(s).strip()}
    if mode == "deny_listed":
        return sender not in {str(s).strip() for s in (rules.get("deny") or []) if str(s).strip()}
    return True


def is_known_mode(rules: dict | None) -> bool:
    """``True`` iff ``rules['mode']`` is one of the recognized values.

    Callers use this to decide whether to emit a "fail-open with
    unknown mode" audit row in addition to allowing the message
    through.
    """
    mode = (rules or {}).get("mode") or "allow_all"
    return mode in {"allow_all", "allow_listed", "deny_listed"}


__all__ = ["is_known_mode", "is_sender_allowed"]
