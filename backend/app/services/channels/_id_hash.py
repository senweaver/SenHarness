"""External application identity hashing for channel uniqueness.

Two channels in different workspaces pointing at the same Discord bot,
Slack app, or DingTalk robot is almost always a misconfiguration: the
inbound webhook traffic will route to whichever channel happens to come
up first, and outbound replies race for the same socket. M0.8 enforces
that the hash of the external app identity is unique per ``kind`` via a
partial unique index on ``channels``.

Hash inputs are kind-specific because each provider exposes a different
"primary key" for its bot:

    discord   → bot_token
    slack     → signing_secret  (signing secret is per Slack app)
    telegram  → bot_token
    teams     → app_id + app_password
    feishu    → app_id + app_secret
    lark      → app_id + app_secret
    dingtalk  → client_id  (Stream) or app_key (Webhook)
    wecom     → corp_id + agent_id
    wechat    → app_id     (iLink session is opaque; use the WeChat
                            developer app id when present)
    qq        → bot_appid
    webhook   → None (generic webhook has no stable external identity;
                      uniqueness is meaningless and rows simply skip
                      the index)

Returns ``None`` when the requested kind / config combination does not
yield a stable identity. Callers must persist whatever this function
returns into ``Channel.external_app_id_hash`` so the partial unique
index does the right thing.
"""

from __future__ import annotations

import hashlib
from typing import Any


def _normalize(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _hash_inputs(kind: str, *identity_parts: str) -> str | None:
    """Combine ``kind`` with the per-kind identity fields.

    Returns ``None`` when *every* identity part is empty so the
    partial unique index correctly skips rows that don't yet have a
    bound bot/app (e.g. WeChat channels created before QR scan,
    discord channels with the bot_token still pending).
    """
    cleaned = [p for p in identity_parts if p]
    if not cleaned:
        return None
    payload = "\x00".join([kind, *cleaned]).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_external_app_id_hash(kind: str, config: dict[str, Any]) -> str | None:
    """Return a stable SHA-256 hex of the external bot/app identity.

    Reads from the **already-decrypted** ``config`` dict, so the caller
    must run ``decrypt_config`` first if the row's ``config_json``
    holds ``enc:v1:`` envelopes.
    """
    cfg = config or {}
    if kind == "discord":
        return _hash_inputs("discord", _normalize(cfg.get("bot_token")))
    if kind == "slack":
        return _hash_inputs("slack", _normalize(cfg.get("signing_secret")))
    if kind == "telegram":
        return _hash_inputs("telegram", _normalize(cfg.get("bot_token")))
    if kind == "teams":
        return _hash_inputs(
            "teams",
            _normalize(cfg.get("app_id")),
            _normalize(cfg.get("app_password")),
        )
    if kind == "feishu":
        return _hash_inputs(
            "feishu",
            _normalize(cfg.get("app_id")),
            _normalize(cfg.get("app_secret")),
        )
    if kind == "lark":
        return _hash_inputs(
            "lark",
            _normalize(cfg.get("app_id")),
            _normalize(cfg.get("app_secret")),
        )
    if kind == "dingtalk":
        # Stream uses ``client_id``; webhook uses ``app_key`` —
        # whichever was provided wins. Both are app-stable.
        primary = _normalize(cfg.get("client_id")) or _normalize(cfg.get("app_key"))
        return _hash_inputs("dingtalk", primary)
    if kind == "wecom":
        return _hash_inputs(
            "wecom",
            _normalize(cfg.get("corp_id")),
            _normalize(cfg.get("agent_id")),
        )
    if kind == "wechat":
        # iLink-bound rows usually have no ``app_id`` at create time;
        # fall back to the bot_token hash so duplicate-bind detection
        # still works after a successful QR scan.
        primary = _normalize(cfg.get("app_id")) or _normalize(cfg.get("bot_token"))
        return _hash_inputs("wechat", primary)
    if kind == "qq":
        return _hash_inputs("qq", _normalize(cfg.get("bot_appid")))
    if kind in ("webhook", "generic_webhook"):
        return None
    return None


__all__ = ["compute_external_app_id_hash"]
