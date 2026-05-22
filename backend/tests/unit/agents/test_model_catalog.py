"""Static model catalog used by ``GET /api/v1/agents/{id}/models``."""

from __future__ import annotations

from app.agents.kernels.model_catalog import (
    CATALOG,
    known_provider_kinds,
    list_models_for_provider,
)


def test_every_known_provider_has_at_least_one_recommended_entry() -> None:
    """The frontend pre-selects ``recommended`` rows; every populated
    provider must surface exactly one so the dropdown isn't blank."""
    for kind in known_provider_kinds():
        rows = list_models_for_provider(kind)
        recommended = [r for r in rows if r.recommended]
        assert len(recommended) >= 1, f"provider {kind!r} has no recommended row in the catalog"


def test_unknown_provider_returns_empty_list() -> None:
    """Garbage-in / garbage-out — caller must handle empty list, not crash."""
    assert list_models_for_provider("totally-not-real") == []
    assert list_models_for_provider("") == []


def test_catalog_ids_round_trip_to_provider_colon_model() -> None:
    """``id`` is the wire token the kernel parses via ``parse_override``;
    it must always match ``"<provider>:<model>"`` exactly."""
    for rows in CATALOG.values():
        for row in rows:
            assert row.id == f"{row.provider}:{row.model}"
            assert ":" in row.id


def test_known_provider_kinds_excludes_empty_buckets() -> None:
    """``custom`` is in CATALOG but has no rows; the discovery helper
    must not advertise it, otherwise the UI shows a blank picker."""
    kinds = known_provider_kinds()
    assert "custom" not in kinds, "empty bucket leaked into provider kinds"
