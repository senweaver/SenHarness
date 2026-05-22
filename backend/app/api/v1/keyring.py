"""Keyring admin — inspect + rotate the KEK.

Platform-admin only. Rotation walks every ``vault_items`` row, unwraps its
DEK under the old KEK version, re-wraps under the new one, and writes back
in batches so a partial failure can resume.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import DBSession
from app.api.v1.admin import require_platform_admin
from app.db.models.identity import Identity
from app.db.models.vault import VaultItem
from app.security.crypto import Sealed, rewrap_for_rotation
from app.security.keyring import Keyring, get_keyring
from app.services import audit as audit_svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/keyring", tags=["keyring"])


# ─── DTOs ─────────────────────────────────────────────────
class KeyringStatus(BaseModel):
    provider: str
    current_kek_version: str
    vault_items_total: int
    vault_items_on_current_kek: int
    rotation_supported: bool


class KeyringRotateOut(BaseModel):
    previous_version: str
    new_version: str
    rewrapped_count: int
    skipped_count: int
    duration_ms: int


# ─── Helpers ─────────────────────────────────────────────
async def _vault_counts(db) -> tuple[int, dict[str, int]]:
    rows = (
        (await db.execute(select(VaultItem.kek_version).where(VaultItem.deleted_at.is_(None))))
        .scalars()
        .all()
    )
    buckets: dict[str, int] = {}
    total = 0
    for v in rows:
        v = str(v or "")
        buckets[v] = buckets.get(v, 0) + 1
        total += 1
    return total, buckets


def _rotation_supported(keyring: Keyring) -> bool:
    """The Env / Passphrase providers explicitly refuse in-process rotation.

    We detect them by provider name rather than catching the exception so the
    UI can disable the Rotate button up front.
    """

    return keyring.provider_name not in {"env", "passphrase"}


# ─── Routes ──────────────────────────────────────────────
@router.get("/status", response_model=KeyringStatus)
async def get_status(
    db: DBSession,
    admin: Identity = Depends(require_platform_admin),
) -> KeyringStatus:
    _ = admin
    keyring = get_keyring()
    total, buckets = await _vault_counts(db)
    on_current = buckets.get(keyring.current_kek_version, 0)
    return KeyringStatus(
        provider=keyring.provider_name,
        current_kek_version=keyring.current_kek_version,
        vault_items_total=total,
        vault_items_on_current_kek=on_current,
        rotation_supported=_rotation_supported(keyring),
    )


@router.post("/rotate", response_model=KeyringRotateOut)
async def rotate_kek(
    db: DBSession,
    request: Request,
    admin: Identity = Depends(require_platform_admin),
) -> KeyringRotateOut:
    """Cut a fresh KEK version and re-wrap every Vault DEK under it.

    Ciphertexts are untouched — the operation is metadata-only on
    ``vault_items.wrapped_dek`` + ``kek_version``. Runs transactionally per
    batch of 200 rows.
    """

    _ = admin
    keyring = get_keyring()
    if not _rotation_supported(keyring):
        # Env / Passphrase providers need an operator-driven handoff (update
        # SENHARNESS_MASTER_KEY in the secret store, then rerun this endpoint
        # after a restart). We surface the hint instead of silently failing.
        from app.core.errors import Conflict

        raise Conflict(
            "keyring_provider_rotation_manual",
            code="keyring.rotate_manual_provider",
            extras={"provider": keyring.provider_name},
        )

    start = datetime.utcnow()
    previous_version = keyring.current_kek_version
    new_version = keyring.rotate()

    rewrapped = 0
    skipped = 0
    offset = 0
    batch_size = 200
    while True:
        rows = (
            (
                await db.execute(
                    select(VaultItem)
                    .where(VaultItem.deleted_at.is_(None))
                    .order_by(VaultItem.created_at.asc())
                    .offset(offset)
                    .limit(batch_size)
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            break
        for row in rows:
            if row.kek_version == new_version:
                # Already rotated (concurrent pass or freshly sealed row).
                continue
            try:
                resealed = rewrap_for_rotation(
                    Sealed(
                        ciphertext=row.ciphertext,
                        wrapped_dek=row.wrapped_dek,
                        kek_version=row.kek_version,
                    ),
                    keyring=keyring,
                )
            except Exception:  # pragma: no cover
                log.exception("rewrap failed for vault_item %s", row.id)
                skipped += 1
                continue
            row.wrapped_dek = resealed.wrapped_dek
            row.kek_version = resealed.kek_version
            rewrapped += 1
        await db.flush()
        offset += len(rows)
    await audit_svc.record(
        db,
        action="keyring.rotate",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="keyring",
        resource_id=None,
        summary=(
            f"rotated KEK {previous_version} → {new_version} "
            f"({rewrapped} rewrapped, {skipped} skipped)"
        ),
        metadata={
            "provider": keyring.provider_name,
            "previous_version": previous_version,
            "new_version": new_version,
            "rewrapped": rewrapped,
            "skipped": skipped,
        },
        request=request,
    )
    await db.commit()

    duration_ms = int((datetime.utcnow() - start).total_seconds() * 1000)
    return KeyringRotateOut(
        previous_version=previous_version,
        new_version=new_version,
        rewrapped_count=rewrapped,
        skipped_count=skipped,
        duration_ms=duration_ms,
    )
