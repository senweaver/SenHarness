"""In-process cache TTL + invalidation primitives."""

from __future__ import annotations

import time

from app.schemas.platform_settings import GeneralSettings
from app.services import platform_settings as ps


def test_cache_round_trip():
    sec = ps.PlatformSettingsSection.GENERAL
    ps.invalidate_local()
    assert ps._cache_get(sec) is None
    ps._cache_set(sec, GeneralSettings(site_name="Acme"))
    cached = ps._cache_get(sec)
    assert cached is not None
    assert cached.site_name == "Acme"


def test_cache_expiry_drops_entry(monkeypatch):
    sec = ps.PlatformSettingsSection.GENERAL
    ps.invalidate_local()
    ps._cache_set(sec, GeneralSettings(site_name="Acme"))
    # Force the cache entry into the past so the next read should miss.
    entry = ps._cache[sec]
    entry.expires_at = time.time() - 1
    assert ps._cache_get(sec) is None
    assert sec not in ps._cache


def test_invalidate_local_clears_specific_section():
    sec = ps.PlatformSettingsSection.GENERAL
    ps._cache_set(sec, GeneralSettings(site_name="A"))
    ps._cache_set(
        ps.PlatformSettingsSection.MEMORY,
        ps.SECTION_SCHEMAS[ps.PlatformSettingsSection.MEMORY](),
    )
    ps.invalidate_local(sec)
    assert ps._cache_get(sec) is None
    assert ps._cache_get(ps.PlatformSettingsSection.MEMORY) is not None
    ps.invalidate_local()
    assert ps._cache == {}
