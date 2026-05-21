"""Unit tests for the provider-model admin paths.

DB-free fast paths:
  - ``delete_provider_model`` precondition (only ``source="manual"`` rows
    can be deleted) verified with a fake ``ProviderModel`` that bails
    before any session work.
  - ``reorder_provider_models`` lazy-create + UUID/model-name dispatch
    verified with a fake repository + session that record what was
    written, so we don't need a Postgres engine.

Schema-shape assertions: lock the wire contract with the frontend so
``sort_order`` / ``capabilities`` / ``ordered_ids`` don't drift silently.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import ValidationFailed
from app.db.models.model_provider import ModelProvider, ProviderModel
from app.schemas.provider import (
    ProviderModelRead,
    ProviderModelReorderRequest,
    ProviderModelUpdate,
)
from app.services import provider as svc


@pytest.mark.asyncio
async def test_delete_blocks_non_manual_source() -> None:
    pm = ProviderModel(
        id=uuid.uuid4(),
        provider_id=uuid.uuid4(),
        model="gpt-5",
        source="static",
        enabled=True,
    )

    with pytest.raises(ValidationFailed) as ei:
        await svc.delete_provider_model(session=None, pm=pm)  # type: ignore[arg-type]
    assert ei.value.code == "provider.model.system_default_undeletable"


def test_provider_model_read_exposes_sort_order() -> None:
    fields = ProviderModelRead.model_fields
    assert "sort_order" in fields
    # Default 0 keeps backward compatibility with rows from pre-migration JSON.
    assert ProviderModelRead.model_fields["sort_order"].default == 0


def test_provider_model_update_accepts_capabilities_and_sort_order() -> None:
    body = ProviderModelUpdate(
        capabilities=["reasoning", "vision"],
        sort_order=3,
    )
    assert body.capabilities == ["reasoning", "vision"]
    assert body.sort_order == 3


def test_reorder_request_defaults_to_empty_list() -> None:
    body = ProviderModelReorderRequest()
    assert body.ordered_ids == []


def test_reorder_request_accepts_mixed_uuids_and_model_names() -> None:
    """Frontend sends model names so catalog-only rows can participate; but
    legacy UUID payloads must still parse."""
    body = ProviderModelReorderRequest(
        ordered_ids=[
            "qwen3.6-plus",
            str(uuid.uuid4()),
            "MiniMax-M2.5",
        ]
    )
    assert len(body.ordered_ids) == 3
    assert body.ordered_ids[0] == "qwen3.6-plus"


# ─── Reorder lazy-create ──────────────────────────────────────────


class _FakeRepo:
    """Minimal stand-in for ``ProviderModelRepository`` exposing only the
    methods ``reorder_provider_models`` calls."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.updates: list[tuple[uuid.UUID, dict]] = []

    async def update(self, obj: ProviderModel, /, **data: object) -> ProviderModel:
        for k, v in data.items():
            setattr(obj, k, v)
        self.updates.append((obj.id, dict(data)))
        return obj

    async def create(self, **data: object) -> ProviderModel:
        row = ProviderModel(id=uuid.uuid4(), **data)  # type: ignore[arg-type]
        self.created.append(dict(data))
        return row


@pytest.mark.asyncio
async def test_reorder_lazy_creates_catalog_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sorting a catalog-only model auto-persists it as ``enabled=False``."""
    provider = ModelProvider(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="bailian_coding",
        name="coding-cn",
        enabled=True,
        credential_type="api_key",
        metadata_json={},
    )
    existing = ProviderModel(
        id=uuid.uuid4(),
        provider_id=provider.id,
        model="qwen3-coder-plus",
        label="Qwen3 Coder Plus",
        family="coding",
        recommended=False,
        enabled=True,
        source="static",
        sort_order=0,
        metadata_json={},
    )

    fake_repo = _FakeRepo()
    monkeypatch.setattr(svc, "ProviderModelRepository", lambda _session: fake_repo)

    list_calls: list[uuid.UUID] = []

    async def fake_list(_session: object, *, provider_id: uuid.UUID) -> list[ProviderModel]:
        list_calls.append(provider_id)
        # First call (initial state): only ``existing`` is in DB.
        # Second call (after reorder writes): include the lazy-created row
        # too; the actual order is irrelevant for this test, we only assert
        # writes.
        if len(list_calls) == 1:
            return [existing]
        return [existing, *[ProviderModel(**row) for row in fake_repo.created]]  # type: ignore[arg-type]

    monkeypatch.setattr(svc, "list_provider_models", fake_list)

    out = await svc.reorder_provider_models(
        session=None,  # type: ignore[arg-type]
        provider=provider,
        ordered_ids=["qwen3.6-plus", "qwen3-coder-plus"],
    )

    assert len(fake_repo.created) == 1, "qwen3.6-plus should have been lazy-persisted"
    created = fake_repo.created[0]
    assert created["model"] == "qwen3.6-plus"
    assert created["enabled"] is False, "lazy-create must not enable the row"
    assert created["source"] == "static"
    assert created["sort_order"] == 0
    assert created["recommended"] is True  # qwen3.6-plus is the recommended pick
    caps = created["metadata_json"]["capabilities"]
    assert "vision" in caps and "reasoning" in caps

    # Existing row was at index 1 → sort_order should now be 1.
    assert any(
        upd_id == existing.id and upd_data.get("sort_order") == 1
        for upd_id, upd_data in fake_repo.updates
    ), fake_repo.updates
    assert out  # post-write list returned


@pytest.mark.asyncio
async def test_reorder_rejects_unknown_model(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ModelProvider(
        id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        kind="bailian_coding",
        name="coding-cn",
        enabled=True,
        credential_type="api_key",
        metadata_json={},
    )

    fake_repo = _FakeRepo()
    monkeypatch.setattr(svc, "ProviderModelRepository", lambda _session: fake_repo)

    async def fake_list(_session: object, *, provider_id: uuid.UUID) -> list[ProviderModel]:
        return []

    monkeypatch.setattr(svc, "list_provider_models", fake_list)

    with pytest.raises(ValidationFailed) as ei:
        await svc.reorder_provider_models(
            session=None,  # type: ignore[arg-type]
            provider=provider,
            ordered_ids=["totally-not-a-real-model"],
        )
    assert ei.value.code == "provider.model.not_found"
