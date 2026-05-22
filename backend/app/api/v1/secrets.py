"""Generic workspace Vault CRUD.

Provider API keys are managed via /providers; this endpoint is for arbitrary
secrets agents need at runtime (webhooks, integration tokens, SMTP passwords,
etc.). Values are envelope-encrypted via ``app.services.vault``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.db.models.vault import VaultItem, VaultItemKind
from app.schemas.secret import SecretCreate, SecretRead, SecretUpdate
from app.services import vault as vault_svc

router = APIRouter(prefix="/secrets", tags=["secrets"])


def _to_read(item: VaultItem) -> SecretRead:
    return SecretRead.model_validate(item)


@router.get("", response_model=list[SecretRead])
async def list_secrets(
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    _identity_id: CurrentIdentityId,
) -> list[SecretRead]:
    stmt = (
        select(VaultItem)
        .where(VaultItem.workspace_id == workspace_id)
        .where(VaultItem.deleted_at.is_(None))
        .order_by(VaultItem.created_at.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [_to_read(r) for r in rows]


@router.post("", response_model=SecretRead, status_code=status.HTTP_201_CREATED)
async def create_secret(
    payload: SecretCreate,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
) -> SecretRead:
    try:
        kind = VaultItemKind(payload.kind)
    except ValueError:
        kind = VaultItemKind.GENERIC
    item = await vault_svc.create_secret(
        db,
        workspace_id=workspace_id,
        owner_identity_id=identity_id,
        name=payload.name,
        plaintext=payload.value,
        kind=kind,
        metadata=payload.metadata_json,
        required_approval=payload.required_approval,
    )
    await db.commit()
    return _to_read(item)


@router.patch("/{secret_id}", response_model=SecretRead)
async def update_secret(
    secret_id: uuid.UUID,
    payload: SecretUpdate,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    _identity_id: CurrentIdentityId,
) -> SecretRead:
    item = await _get_or_404(db, secret_id, workspace_id)
    if payload.value is not None:
        item = await vault_svc.replace_secret(db, item=item, plaintext=payload.value)
    if payload.metadata_json is not None:
        item.metadata_json = payload.metadata_json
    if payload.required_approval is not None:
        item.required_approval = payload.required_approval
    await db.commit()
    await db.refresh(item)
    return _to_read(item)


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: uuid.UUID,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    _identity_id: CurrentIdentityId,
) -> None:
    item = await _get_or_404(db, secret_id, workspace_id)
    await db.delete(item)
    await db.commit()


@router.post("/{secret_id}/reveal", response_model=dict)
async def reveal_secret(
    secret_id: uuid.UUID,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    _identity_id: CurrentIdentityId,
) -> dict[str, str]:
    """Out-of-band reveal. Only respond with the plaintext; never persist it in logs."""
    item = await _get_or_404(db, secret_id, workspace_id)
    plaintext = await vault_svc.reveal_secret(item)
    return {"value": plaintext}


async def _get_or_404(db, secret_id: uuid.UUID, workspace_id: uuid.UUID) -> VaultItem:
    stmt = (
        select(VaultItem)
        .where(VaultItem.id == secret_id)
        .where(VaultItem.workspace_id == workspace_id)
        .where(VaultItem.deleted_at.is_(None))
    )
    item = (await db.execute(stmt)).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found.")
    return item
