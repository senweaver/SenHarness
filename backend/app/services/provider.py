"""Model provider service: CRUD + key ingestion via Vault + model discovery."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.kernels.model_catalog import CATALOG, CatalogModel
from app.agents.kernels.provider_catalog import (
    api_key_env_for,
    default_base_url_for,
    family_of,
    get_entry,
    is_known_kind,
    supports_discover,
)
from app.core.errors import NotFound, ValidationFailed
from app.core.url_safety import UnsafeURLError, assert_safe_url
from app.db.models.model_provider import (
    CredentialType,
    ModelKey,
    ModelProvider,
    ProviderModel,
)
from app.db.models.vault import VaultItemKind
from app.db.repository import AsyncRepository
from app.repositories.provider import (
    ModelKeyRepository,
    ModelProviderRepository,
    ProviderModelRepository,
)
from app.services import vault as vault_svc

log = logging.getLogger(__name__)

_ = api_key_env_for, default_base_url_for, get_entry  # public re-exports for callers


# Discover network knobs — keep small so a slow upstream doesn't pile
# requests in the FastAPI worker pool.
_DISCOVER_HTTP_TIMEOUT_S = 8.0
_DISCOVER_MAX_MODELS = 200


async def list_providers(session: AsyncSession, *, workspace_id: uuid.UUID) -> list[ModelProvider]:
    repo = ModelProviderRepository(session)
    rows = await repo.list(workspace_id=workspace_id, limit=200)
    return sorted(rows, key=lambda p: (p.sort_order, p.created_at))


async def reorder_providers(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    ordered_ids: list[uuid.UUID],
) -> list[ModelProvider]:
    """Persist drag-to-reorder for the workspace's provider list.

    Each id in ``ordered_ids`` must already belong to the workspace
    (deleted rows are silently skipped). Rows present in the workspace
    but missing from ``ordered_ids`` keep their current ``sort_order``
    so a partial submit doesn't accidentally renumber the rest.
    Renumbering is dense (0, 1, 2, ...) so a follow-up create can pick
    ``sort_order = max + 1`` cheaply.
    """
    repo = ModelProviderRepository(session)
    rows = await repo.list(workspace_id=workspace_id, limit=500)
    by_id: dict[uuid.UUID, ModelProvider] = {row.id: row for row in rows}

    rank = 0
    for provider_id in ordered_ids:
        row = by_id.get(provider_id)
        if row is None:
            continue
        if int(row.sort_order) != rank:
            row.sort_order = rank
        rank += 1
    await session.flush()
    fresh = await repo.list(workspace_id=workspace_id, limit=500)
    return sorted(fresh, key=lambda p: (p.sort_order, p.created_at))


async def get_or_404(
    session: AsyncSession, provider_id: uuid.UUID, *, workspace_id: uuid.UUID
) -> ModelProvider:
    repo = ModelProviderRepository(session)
    obj = await repo.get(provider_id)
    if obj is None or obj.workspace_id != workspace_id:
        raise NotFound("provider_not_found", code="provider.not_found")
    return obj


async def create_provider(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    owner_identity_id: uuid.UUID | None,
    kind: str,
    name: str,
    base_url: str | None = None,
    default_model: str | None = None,
    enabled: bool = True,
    credential_type: str | None = None,
    country_code: str | None = None,
    metadata_json: dict | None = None,
    api_key: str | None = None,
) -> ModelProvider:
    cleaned_kind = (kind or "").strip().lower()
    if not is_known_kind(cleaned_kind):
        raise ValidationFailed(f"unknown provider kind: {kind!r}", code="provider.unknown_kind")

    entry = get_entry(cleaned_kind)
    canonical = entry.kind if entry is not None else cleaned_kind
    if credential_type is None and entry is not None:
        credential_type = entry.credential_type
    if country_code is None and entry is not None:
        country_code = entry.country_code

    prov_repo = ModelProviderRepository(session)
    provider = await prov_repo.create(
        workspace_id=workspace_id,
        kind=canonical,
        name=name,
        base_url=base_url,
        default_model=default_model,
        enabled=enabled,
        credential_type=credential_type or CredentialType.API_KEY.value,
        country_code=country_code,
        metadata_json=metadata_json or {},
    )
    if api_key:
        vault_kind = (
            VaultItemKind.OAUTH
            if (credential_type or "").lower() == "oauth_token"
            else VaultItemKind.API_KEY
        )
        vault_item = await vault_svc.create_secret(
            session,
            workspace_id=workspace_id,
            owner_identity_id=owner_identity_id,
            name=f"provider/{provider.id}/default",
            plaintext=api_key,
            kind=vault_kind,
            metadata={"provider_id": str(provider.id)},
        )
        key_repo: AsyncRepository[ModelKey] = ModelKeyRepository(session)
        await key_repo.create(
            provider_id=provider.id,
            name="default",
            vault_item_id=vault_item.id,
        )
    return provider


async def update_provider(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    name: str | None = None,
    base_url: str | None = None,
    default_model: str | None = None,
    enabled: bool | None = None,
    credential_type: str | None = None,
    country_code: str | None = None,
    metadata_json: dict | None = None,
    api_key: str | None = None,
) -> ModelProvider:
    prov_repo = ModelProviderRepository(session)
    updates: dict = {}
    if name is not None:
        updates["name"] = name
    if base_url is not None:
        updates["base_url"] = base_url
    if default_model is not None:
        updates["default_model"] = default_model
    if enabled is not None:
        updates["enabled"] = enabled
    if credential_type is not None:
        updates["credential_type"] = credential_type
    if country_code is not None:
        updates["country_code"] = country_code
    if metadata_json is not None:
        updates["metadata_json"] = metadata_json
    if updates:
        await prov_repo.update(provider, **updates)

    if api_key:
        # Overwrite primary key or create one.
        key_repo = ModelKeyRepository(session)
        key = await key_repo.get_by(provider_id=provider.id, name="default")
        if key and key.vault_item_id:
            from app.db.models.vault import VaultItem

            vault_repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
            existing_item = await vault_repo.get(key.vault_item_id)
            if existing_item is not None:
                await vault_svc.replace_secret(session, item=existing_item, plaintext=api_key)
        else:
            vault_item = await vault_svc.create_secret(
                session,
                workspace_id=provider.workspace_id,
                owner_identity_id=None,
                name=f"provider/{provider.id}/default",
                plaintext=api_key,
            )
            await key_repo.create(
                provider_id=provider.id, name="default", vault_item_id=vault_item.id
            )

    return provider


async def delete_provider(session: AsyncSession, *, provider: ModelProvider) -> None:
    await ModelProviderRepository(session).soft_delete(provider)


async def provider_has_key(session: AsyncSession, *, provider_id: uuid.UUID) -> bool:
    repo = ModelKeyRepository(session)
    return await repo.exists(provider_id=provider_id, enabled=True)


# ─── ProviderModel CRUD ──────────────────────────────────────────


async def list_provider_models(
    session: AsyncSession, *, provider_id: uuid.UUID
) -> list[ProviderModel]:
    repo = ProviderModelRepository(session)
    rows = await repo.list(
        provider_id=provider_id,
        order_by=(ProviderModel.sort_order.asc(), ProviderModel.created_at.asc()),
        limit=_DISCOVER_MAX_MODELS,
    )
    return list(rows)


async def get_provider_model(
    session: AsyncSession, model_id: uuid.UUID, *, provider_id: uuid.UUID
) -> ProviderModel:
    repo = ProviderModelRepository(session)
    row = await repo.get(model_id)
    if row is None or row.provider_id != provider_id:
        raise NotFound("provider_model_not_found", code="provider.model.not_found")
    return row


async def update_provider_model(
    session: AsyncSession,
    *,
    pm: ProviderModel,
    enabled: bool | None = None,
    label: str | None = None,
    recommended: bool | None = None,
    context_window: int | None = None,
    capabilities: list[str] | None = None,
    sort_order: int | None = None,
) -> ProviderModel:
    repo = ProviderModelRepository(session)
    updates: dict = {}
    if enabled is not None:
        updates["enabled"] = enabled
    if label is not None:
        updates["label"] = label
    if recommended is not None:
        updates["recommended"] = recommended
    if context_window is not None:
        updates["context_window"] = context_window
    if sort_order is not None:
        updates["sort_order"] = sort_order
    if capabilities is not None:
        # Re-bind the JSONB column with a fresh dict so SQLAlchemy detects the
        # write — mutating ``pm.metadata_json`` in place wouldn't trigger a
        # flush.
        meta = dict(pm.metadata_json or {})
        cleaned = [c.strip().lower() for c in capabilities if c and c.strip()]
        meta["capabilities"] = cleaned
        updates["metadata_json"] = meta
    if updates:
        await repo.update(pm, **updates)
    return pm


async def delete_provider_model(session: AsyncSession, *, pm: ProviderModel) -> None:
    """Delete a single provider_model row.

    Only operator-typed (`source="manual"`) rows can be deleted. Rows seeded
    from the static catalog (`static`) or upstream `/v1/models` discover
    (`remote`) are considered system defaults and stay in the table; toggle
    them off via ``enabled=False`` instead.
    """
    if pm.source != "manual":
        raise ValidationFailed(
            "system default model cannot be deleted",
            code="provider.model.system_default_undeletable",
        )
    repo = ProviderModelRepository(session)
    await repo.hard_delete(pm)


async def reorder_provider_models(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    ordered_ids: list[str],
) -> list[ProviderModel]:
    """Persist drag-to-reorder by writing a 0-based ``sort_order`` per row.

    Each ``ordered_ids`` entry is either a ``ProviderModel.id`` UUID
    (already-persisted row) or the upstream ``model`` identifier (e.g.
    ``"qwen3.5-plus"``). Catalog entries that aren't yet persisted are
    lazily inserted as ``enabled=False, source="static"`` rows so the user
    can sort builtin models without first enabling them.
    """
    repo = ProviderModelRepository(session)
    rows = await list_provider_models(session, provider_id=provider.id)
    by_uuid: dict[uuid.UUID, ProviderModel] = {row.id: row for row in rows}
    by_model: dict[str, ProviderModel] = {row.model: row for row in rows}
    catalog_index = {row.model: row for row in CATALOG.get(str(provider.kind), [])}

    for idx, identifier in enumerate(ordered_ids):
        token = (identifier or "").strip()
        if not token:
            continue
        row: ProviderModel | None = None
        try:
            row = by_uuid.get(uuid.UUID(token))
        except ValueError:
            row = None
        if row is None:
            row = by_model.get(token)
        if row is None:
            meta = catalog_index.get(token)
            if meta is None:
                raise ValidationFailed(
                    f"unknown model {token!r} for provider {provider.id}",
                    code="provider.model.not_found",
                )
            meta_json: dict = {
                "category": meta.category,
                "capabilities": list(meta.capabilities),
            }
            if meta.pricing:
                meta_json["pricing"] = list(meta.pricing)
            row = await repo.create(
                provider_id=provider.id,
                model=meta.model,
                label=meta.name,
                family=meta.family,
                recommended=meta.recommended,
                enabled=False,
                context_window=meta.context_window,
                source="static",
                sort_order=idx,
                metadata_json=meta_json,
            )
            by_uuid[row.id] = row
            by_model[row.model] = row
            continue
        if row.sort_order != idx:
            await repo.update(row, sort_order=idx)
    return await list_provider_models(session, provider_id=provider.id)


async def add_manual_model(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    model: str,
    label: str | None = None,
    family: str | None = None,
    context_window: int | None = None,
    enabled: bool = True,
) -> ProviderModel:
    """Manual catch-all: operator-typed model that discover couldn't find."""
    repo = ProviderModelRepository(session)
    existing = await repo.get_by(provider_id=provider.id, model=model)
    if existing is not None:
        return await update_provider_model(
            session,
            pm=existing,
            enabled=enabled,
            label=label,
            context_window=context_window,
        )
    return await repo.create(
        provider_id=provider.id,
        model=model,
        label=label,
        family=family,
        context_window=context_window,
        enabled=enabled,
        recommended=False,
        source="manual",
        metadata_json={"category": _guess_category(model), "capabilities": []},
    )


# ─── Discover ────────────────────────────────────────────────────


async def discover_models(session: AsyncSession, *, provider: ModelProvider) -> dict:
    """Return what models the upstream advertises plus what's already in DB.

    Strategy by family:
      - ``openai-compatible``: GET ``${base_url}/v1/models`` (or ``${base}/models``
        when the base already ends in ``/v1``) using the vault-stored API key
        as a Bearer token. Falls back to static catalog on HTTP error.
      - everything else: serve the static `model_catalog.CATALOG` list.
    """
    kind = str(provider.kind)
    existing_rows = await list_provider_models(session, provider_id=provider.id)
    existing_ids = [r.model for r in existing_rows]

    discovered: list[dict] = []
    source = "static"
    error: str | None = None

    if supports_discover(kind):
        api_key = await _provider_api_key(session, provider=provider)
        base_url = (provider.base_url or default_base_url_for(kind) or "").rstrip("/")
        if not base_url:
            error = "missing_base_url"
        elif not api_key:
            error = "missing_api_key"
        else:
            try:
                models_url = (
                    f"{base_url}/models"
                    if base_url.endswith("/v1") or base_url.endswith("/openai")
                    else f"{base_url}/v1/models"
                )
                assert_safe_url(models_url, allow_private=True)
                discovered = await _fetch_remote_models(models_url, api_key=api_key)
                source = "remote"
            except UnsafeURLError as e:
                # i18n keys can't contain '.' (next-intl uses '.' as nesting),
                # so flatten ``ssrf.private_address`` → ``ssrf_private_address``.
                error = e.code.replace(".", "_")
            except httpx.HTTPError as e:
                log.warning("discover HTTP error provider=%s err=%s", provider.id, e)
                error = "remote_unreachable"

    if not discovered:
        discovered = _static_models_for(kind)

    enriched = _merge_with_catalog(kind, discovered, existing_ids)
    return {
        "kind": kind,
        "source": source,
        "discovered": enriched,
        "existing_ids": existing_ids,
        "error": error,
    }


async def test_connectivity(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    model: str | None,
) -> dict:
    """Send a minimal request to the provider to verify key + base_url + model.

    Strategy:
      - OpenAI-compatible providers: GET ``/v1/models`` (cheaper than chat).
      - Anthropic / Google / Bedrock / Cohere / Mistral: build the pydantic-ai
        Model object and check it constructs without error. A real chat probe
        is intentionally skipped — those upstreams charge per token.

    Returns a dict matching ``ProviderTestResponse``.
    """
    import time

    from app.agents.kernels.model_client import ResolvedModel, build_pydantic_ai_model

    kind = str(provider.kind)
    api_key = await _provider_api_key(session, provider=provider)
    base_url = (provider.base_url or default_base_url_for(kind) or "").rstrip("/")

    if not api_key:
        return {"ok": False, "error": "missing_api_key"}

    start = time.monotonic()
    try:
        if supports_discover(kind):
            if not base_url:
                return {"ok": False, "error": "missing_base_url"}
            url = (
                f"{base_url}/models"
                if base_url.endswith("/v1") or base_url.endswith("/openai")
                else f"{base_url}/v1/models"
            )
            assert_safe_url(url, allow_private=True)
            async with httpx.AsyncClient(timeout=_DISCOVER_HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, headers={"Authorization": f"Bearer {api_key}"})
            elapsed = int((time.monotonic() - start) * 1000)
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "latency_ms": elapsed,
                    "error": f"http_{resp.status_code}",
                    "detail": resp.text[:200],
                }
            return {"ok": True, "latency_ms": elapsed, "detail": "models endpoint reachable"}

        # Non-OpenAI families: just verify the SDK accepts the credential
        # (catches expired keys, missing extras, etc.).
        resolved = ResolvedModel(
            provider_kind=kind,
            model_name=model or provider.default_model or "default",
            api_key=api_key,
            base_url=base_url or None,
            source="db",
        )
        built = build_pydantic_ai_model(resolved)
        elapsed = int((time.monotonic() - start) * 1000)
        if built is None:
            return {
                "ok": False,
                "latency_ms": elapsed,
                "error": "model_build_failed",
            }
        return {
            "ok": True,
            "latency_ms": elapsed,
            "detail": f"sdk built {type(built).__name__}",
        }
    except UnsafeURLError as e:
        return {"ok": False, "error": e.code.replace(".", "_")}
    except httpx.HTTPError as e:
        log.warning("connectivity probe failed provider=%s err=%s", provider.id, e)
        return {"ok": False, "error": "remote_unreachable", "detail": str(e)[:200]}
    except Exception as e:  # pragma: no cover
        log.warning("connectivity probe error provider=%s err=%s", provider.id, e)
        return {"ok": False, "error": "internal_error", "detail": str(e)[:200]}


async def apply_discovered_models(
    session: AsyncSession,
    *,
    provider: ModelProvider,
    model_ids: Iterable[str],
    replace: bool,
) -> list[ProviderModel]:
    """Batch-upsert ``model_ids`` into ``provider_models``.

    When ``replace=True``, models not in ``model_ids`` get ``enabled=False``
    (rows are kept for history / future re-enable).
    """
    repo = ProviderModelRepository(session)
    selected = {m for m in model_ids if m}
    if not selected and not replace:
        return await list_provider_models(session, provider_id=provider.id)

    kind = str(provider.kind)
    catalog_index = {row.model: row for row in CATALOG.get(kind, [])}

    existing = await list_provider_models(session, provider_id=provider.id)
    by_model: dict[str, ProviderModel] = {row.model: row for row in existing}

    out: list[ProviderModel] = []
    for model_id in selected:
        meta = catalog_index.get(model_id)
        existing_row = by_model.get(model_id)
        if existing_row is not None:
            await repo.update(existing_row, enabled=True)
            out.append(existing_row)
            continue
        meta_json: dict = {
            "category": meta.category if meta else _guess_category(model_id),
            "capabilities": list(meta.capabilities) if meta else [],
        }
        if meta and meta.pricing:
            meta_json["pricing"] = list(meta.pricing)
        new_row = await repo.create(
            provider_id=provider.id,
            model=model_id,
            label=meta.name if meta else None,
            family=meta.family if meta else None,
            recommended=meta.recommended if meta else False,
            enabled=True,
            context_window=meta.context_window if meta else None,
            source="remote" if meta is None else "static",
            metadata_json=meta_json,
        )
        out.append(new_row)

    if replace:
        for row in existing:
            if row.model not in selected and row.enabled:
                await repo.update(row, enabled=False)

    return await list_provider_models(session, provider_id=provider.id)


# ─── Internals ──────────────────────────────────────────────────


async def _provider_api_key(session: AsyncSession, *, provider: ModelProvider) -> str | None:
    """Return the plaintext default key for ``provider`` (or None)."""
    key_repo = ModelKeyRepository(session)
    key = await key_repo.get_by(provider_id=provider.id, name="default")
    if key is None or key.vault_item_id is None:
        return None
    from app.db.models.vault import VaultItem

    vault_repo: AsyncRepository[VaultItem] = AsyncRepository(session, VaultItem)
    item = await vault_repo.get(key.vault_item_id)
    if item is None:
        return None
    try:
        return await vault_svc.reveal_secret(item)
    except Exception as e:  # pragma: no cover
        log.warning("vault reveal failed for provider %s: %s", provider.id, e)
        return None


async def _fetch_remote_models(url: str, *, api_key: str) -> list[dict]:
    """Call ``GET ${base}/models`` and return the OpenAI-shaped ``data`` rows.

    Tolerates two shapes:
      - OpenAI: ``{"data": [{"id": "gpt-5", ...}]}``
      - Some self-hosted gateways: ``[{"id": "...", ...}, ...]``
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=_DISCOVER_HTTP_TIMEOUT_S) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        body = resp.json()

    rows: list[dict]
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        rows = body["data"]
    elif isinstance(body, list):
        rows = body
    else:
        rows = []

    out: list[dict] = []
    for r in rows[:_DISCOVER_MAX_MODELS]:
        if not isinstance(r, dict):
            continue
        model_id = r.get("id") or r.get("model") or r.get("name")
        if not isinstance(model_id, str) or not model_id:
            continue
        ctx = r.get("context_length") or r.get("context_window")
        out.append(
            {
                "model": model_id,
                "label": r.get("name") or r.get("display_name") or None,
                "context_window": int(ctx) if isinstance(ctx, (int, float)) else None,
            }
        )
    return out


def _static_models_for(kind: str) -> list[dict]:
    rows: list[CatalogModel] = list(CATALOG.get(kind, []))
    if not rows:
        # Try the alias-resolved canonical kind first (e.g. ``moonshotai`` →
        # ``moonshot`` where the catalog rows actually live), then the
        # pydantic-ai kind as a last resort.
        entry = get_entry(kind)
        if entry is not None:
            if entry.kind != kind:
                rows = list(CATALOG.get(entry.kind, []))
            if not rows and entry.pydantic_ai_kind:
                rows = list(CATALOG.get(entry.pydantic_ai_kind, []))
    out: list[dict] = []
    for row in rows:
        out.append(
            {
                "model": row.model,
                "label": row.name,
                "family": row.family,
                "recommended": row.recommended,
                "context_window": row.context_window,
                "category": row.category,
                "capabilities": list(row.capabilities),
                "pricing": list(row.pricing) if row.pricing else None,
            }
        )
    return out


def _merge_with_catalog(kind: str, discovered: list[dict], existing_ids: list[str]) -> list[dict]:
    """Enrich raw discover rows with catalog metadata (family, recommended)."""
    catalog_index = {row.model: row for row in CATALOG.get(kind, [])}
    existing_set = set(existing_ids)
    out: list[dict] = []
    for row in discovered:
        model_id = row["model"]
        meta = catalog_index.get(model_id)
        out.append(
            {
                "model": model_id,
                "label": row.get("label") or (meta.name if meta else None),
                "family": row.get("family") or (meta.family if meta else None),
                "recommended": bool(row.get("recommended"))
                or (meta.recommended if meta else False),
                "in_db": model_id in existing_set,
                "context_window": row.get("context_window")
                or (meta.context_window if meta else None),
                "category": row.get("category")
                or (meta.category if meta else _guess_category(model_id)),
                "capabilities": row.get("capabilities")
                or (list(meta.capabilities) if meta else []),
                "pricing": row.get("pricing")
                or (list(meta.pricing) if meta and meta.pricing else None),
            }
        )
    # Stable sort: recommended first, then alphabetical.
    out.sort(key=lambda r: (not r["recommended"], r["model"].lower()))
    return out


def _guess_category(model: str) -> str:
    """Best-effort categorisation of a model id when the catalog has no entry."""
    m = model.lower()
    if any(t in m for t in ("embed", "embedding", "rerank", "voyage")):
        return "embedding"
    if any(t in m for t in ("whisper", "transcribe", "asr", "stt")):
        return "asr"
    if any(t in m for t in ("tts", "speech", "tts-1", "voice")):
        return "tts"
    if any(t in m for t in ("image", "dall-e", "stable-diffusion", "flux", "midjourney", "imagen")):
        return "image"
    if any(t in m for t in ("video", "sora", "runway", "veo")):
        return "video"
    return "chat"


# Suppress F401 — `family_of` is part of the service's public surface even if
# it isn't used inside this file.
_ = family_of
