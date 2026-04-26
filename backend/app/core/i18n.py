"""Error-code based i18n (backend returns stable codes; frontend renders via next-intl).

Backend never interpolates localized strings directly; it only emits::

    {"code": "auth.token_expired", "detail": "token_expired", "extras": {...}}
"""

from __future__ import annotations

SUPPORTED_LOCALES = ("zh-CN", "en-US")


def pick_locale(accept_language: str | None, default: str = "zh-CN") -> str:
    if not accept_language:
        return default
    for part in accept_language.split(","):
        lang = part.split(";", 1)[0].strip()
        if lang in SUPPORTED_LOCALES:
            return lang
    # best-effort coarse fallback e.g. "en" -> "en-US"
    for part in accept_language.split(","):
        lang = part.split(";", 1)[0].strip().split("-", 1)[0]
        for supported in SUPPORTED_LOCALES:
            if supported.startswith(lang + "-") or supported == lang:
                return supported
    return default
