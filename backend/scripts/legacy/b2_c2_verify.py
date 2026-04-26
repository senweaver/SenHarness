"""B2 + C2 verification — nightly GC + Vault CRUD.

B2:
  1. Seed an attachment soft-deleted 60 days ago + a fresh attachment.
  2. ``run_full_sweep(dry_run=True)`` reports candidates but doesn't delete.
  3. ``run_full_sweep(dry_run=False)`` hard-deletes the stale row + on-disk
     blob; the fresh row stays.
  4. Same for an audit event past ``AUDIT_RETENTION_DAYS``.

C2:
  5. ``POST /secrets`` creates a vault item; subsequent ``GET /secrets`` lists
     it without plaintext.
  6. ``POST /secrets/{id}/reveal`` round-trips the plaintext.
  7. ``PATCH /secrets/{id}`` rotates the value AND updates description metadata.
  8. ``DELETE /secrets/{id}`` removes the row.

Run:  ``python -m scripts.b2_c2_verify``
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta
from pathlib import Path

logging.basicConfig(level=logging.WARNING)

import httpx

from app.core.config import settings
from app.core.security import create_access_token, hash_password, utcnow_naive
from app.db.models.attachment import Attachment, AttachmentKind
from app.db.models.audit import AuditEvent
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.models.workspace import DEFAULT_BRANDING, Workspace, WorkspacePlan
from app.db.session import get_session_factory
from app.db.repository import AsyncRepository
from app.main import app
from app.services import gc as gc_svc


async def setup() -> dict:
    factory = get_session_factory()
    async with factory() as db:
        ws = Workspace(
            slug=f"b2-{uuid.uuid4().hex[:6]}",
            name="B2/C2 Test",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add(ws)
        await db.flush()
        user = Identity(
            email=f"b2-{uuid.uuid4().hex[:6]}@b2.local",
            name="b2-user",
            password_hash=hash_password("x" * 12),
            status=IdentityStatus.ACTIVE,
            platform_role=PlatformRole.USER,
            profile_json={},
        )
        db.add(user)
        await db.flush()
        db.add(
            Membership(
                workspace_id=ws.id,
                identity_id=user.id,
                role=BuiltinRole.ADMIN.value,
                status=MembershipStatus.ACTIVE,
            )
        )
        await db.commit()
        return {"ws_id": str(ws.id), "user_id": str(user.id)}


async def _seed_stale_attachment(workspace_id: uuid.UUID) -> tuple[uuid.UUID, Path]:
    """Drop a tiny blob on disk + create a soft-deleted-60-days-ago row."""
    storage_root = Path(settings.STORAGE_LOCAL_PATH) / "attachments" / str(workspace_id) / "test"
    storage_root.mkdir(parents=True, exist_ok=True)
    blob = storage_root / f"{uuid.uuid4()}.txt"
    blob.write_bytes(b"stale-blob")
    factory = get_session_factory()
    async with factory() as db:
        att = await AsyncRepository(db, Attachment).create(
            workspace_id=workspace_id,
            session_id=None,
            uploader_identity_id=None,
            filename="stale.txt",
            mime_type="text/plain",
            size_bytes=10,
            kind=AttachmentKind.DOCUMENT,
            storage_uri=str(blob),
            sha256=None,
            metadata_json={},
        )
        # Soft-delete and back-date so the GC picks it up.
        att.deleted_at = utcnow_naive() - timedelta(days=60)
        await db.flush([att])
        await db.commit()
        return att.id, blob


async def _seed_fresh_attachment(workspace_id: uuid.UUID) -> uuid.UUID:
    storage_root = Path(settings.STORAGE_LOCAL_PATH) / "attachments" / str(workspace_id) / "test"
    storage_root.mkdir(parents=True, exist_ok=True)
    blob = storage_root / f"{uuid.uuid4()}.txt"
    blob.write_bytes(b"fresh")
    factory = get_session_factory()
    async with factory() as db:
        att = await AsyncRepository(db, Attachment).create(
            workspace_id=workspace_id,
            session_id=None,
            uploader_identity_id=None,
            filename="fresh.txt",
            mime_type="text/plain",
            size_bytes=5,
            kind=AttachmentKind.DOCUMENT,
            storage_uri=str(blob),
            sha256=None,
            metadata_json={},
        )
        await db.commit()
        return att.id


async def _seed_old_audit() -> uuid.UUID:
    factory = get_session_factory()
    async with factory() as db:
        ev = AuditEvent(
            workspace_id=None,
            actor_identity_id=None,
            action="b2.test_old",
            resource_type="test",
            resource_id=None,
            summary="ancient",
            metadata_json={},
            ip_address=None,
            user_agent=None,
        )
        db.add(ev)
        await db.flush()
        ev.created_at = utcnow_naive() - timedelta(days=settings.AUDIT_RETENTION_DAYS + 5)
        await db.flush([ev])
        await db.commit()
        return ev.id


def tok(identity_id: str, workspace_id: str) -> str:
    t, _exp, _jti = create_access_token(
        identity_id=identity_id, workspace_id=workspace_id, extra={}
    )
    return t


async def verify_b2(ctx: dict) -> None:
    ws_id = uuid.UUID(ctx["ws_id"])
    stale_id, stale_blob = await _seed_stale_attachment(ws_id)
    fresh_id = await _seed_fresh_attachment(ws_id)
    audit_id = await _seed_old_audit()

    dry = await gc_svc.run_full_sweep(dry_run=True)
    assert dry["dry_run"] is True
    assert dry["attachments"]["candidates"] >= 1, dry
    assert dry["attachments"]["deleted"] == 0, dry
    assert dry["audit_events"]["candidates"] >= 1, dry
    print(
        f"  [B2.1] dry-run: att candidates={dry['attachments']['candidates']} "
        f"audit candidates={dry['audit_events']['candidates']}  (PASS)"
    )

    real = await gc_svc.run_full_sweep(dry_run=False)
    assert real["attachments"]["deleted"] >= 1, real
    print(
        f"  [B2.2] real run: att deleted={real['attachments']['deleted']} "
        f"blobs_removed={real['attachments']['blobs_removed']}"
    )

    # Stale blob should be gone.
    assert not stale_blob.exists(), f"stale blob still present: {stale_blob}"
    print("  [B2.3] stale blob removed from disk  (PASS)")

    # Fresh row still present.
    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import select  # noqa: PLC0415

        fresh_still = (
            await db.execute(select(Attachment).where(Attachment.id == fresh_id))
        ).scalar_one_or_none()
        assert fresh_still is not None, "fresh attachment was incorrectly GC'd"
        ancient_audit = (
            await db.execute(select(AuditEvent).where(AuditEvent.id == audit_id))
        ).scalar_one_or_none()
        assert ancient_audit is None, "ancient audit event was not GC'd"
    print("  [B2.4] fresh att kept; ancient audit gone  (PASS)")


async def verify_c2(ctx: dict) -> None:
    ws_id = ctx["ws_id"]
    user_tok = tok(ctx["user_id"], ws_id)
    headers = {"Authorization": f"Bearer {user_tok}", "X-Workspace-Id": ws_id}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 5) Create
        r = await client.post(
            "/api/v1/secrets",
            json={
                "name": "stripe_test_key",
                "value": "sk_test_super_secret_42",
                "kind": "api_key",
                "metadata_json": {"description": "Stripe sandbox"},
            },
            headers=headers,
        )
        assert r.status_code == 201, r.text
        secret = r.json()
        secret_id = secret["id"]
        assert secret["name"] == "stripe_test_key"
        # plaintext should never appear in list/read responses
        assert "value" not in secret, secret
        print("  [C2.5] create secret  (PASS)")

        # List should include it
        r2 = await client.get("/api/v1/secrets", headers=headers)
        assert r2.status_code == 200
        names = {s["name"] for s in r2.json()}
        assert "stripe_test_key" in names

        # 6) Reveal
        r3 = await client.post(
            f"/api/v1/secrets/{secret_id}/reveal", json={}, headers=headers
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["value"] == "sk_test_super_secret_42"
        print("  [C2.6] reveal returns plaintext  (PASS)")

        # 7) Patch — rotate value + update description
        r4 = await client.patch(
            f"/api/v1/secrets/{secret_id}",
            json={
                "value": "sk_test_rotated_99",
                "metadata_json": {"description": "Stripe sandbox (rotated)"},
                "required_approval": True,
            },
            headers=headers,
        )
        assert r4.status_code == 200, r4.text
        patched = r4.json()
        assert patched["required_approval"] is True
        assert patched["metadata_json"]["description"].endswith("(rotated)")

        r5 = await client.post(
            f"/api/v1/secrets/{secret_id}/reveal", json={}, headers=headers
        )
        assert r5.json()["value"] == "sk_test_rotated_99"
        print("  [C2.7] rotate + metadata update  (PASS)")

        # 8) Delete
        r6 = await client.delete(f"/api/v1/secrets/{secret_id}", headers=headers)
        assert r6.status_code == 204
        r7 = await client.get("/api/v1/secrets", headers=headers)
        names_after = {s["name"] for s in r7.json()}
        assert "stripe_test_key" not in names_after
        print("  [C2.8] delete  (PASS)")


async def main() -> None:
    ctx = await setup()
    await verify_b2(ctx)
    await verify_c2(ctx)
    print("\n[PASS] B2 + C2 verification complete")


if __name__ == "__main__":
    asyncio.run(main())
