"""B1 verification — attachment → knowledge import.

Checkpoints:

1. Seed workspace + user + attachment (textual `.md` file stored on disk).
2. Create a knowledge collection.
3. Call ``POST /knowledge/collections/{id}/ingest_attachment`` → response is
   a doc with ``status=ready``, ``source_kind=file``, and a non-zero chunk
   count; ``metadata_json`` carries the attachment id + filename.
4. Second ingest of an audio-kind attachment fails with HTTP 415 and
   ``detail.code == "unsupported_kind"``.
5. Non-existent attachment_id → 404.

Run:  ``python -m scripts.b1_verify``
"""
from __future__ import annotations

import asyncio
import logging
import uuid

logging.basicConfig(level=logging.WARNING)

import httpx

from app.core.security import create_access_token, hash_password
from app.db.models.attachment import AttachmentKind
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.knowledge import KnowledgeCollection
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.models.workspace import DEFAULT_BRANDING, Workspace, WorkspacePlan
from app.db.session import get_session_factory
from app.db.repository import AsyncRepository
from app.main import app


DOC_TEXT = (
    "# SenHarness runbook\n\n"
    "This is a small markdown document used to verify the attachment → RAG "
    "ingest path end-to-end. It has enough text to produce multiple chunks "
    "when the default chunk size (~800 chars) kicks in, so the test also "
    "exercises the chunker.\n\n"
    + "alpha bravo charlie delta echo foxtrot golf hotel india juliet "
    * 40
)


async def setup() -> dict:
    factory = get_session_factory()
    async with factory() as db:
        ws = Workspace(
            slug=f"b1-{uuid.uuid4().hex[:6]}",
            name="B1 Test",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add(ws)
        await db.flush()

        user = Identity(
            email=f"b1-{uuid.uuid4().hex[:6]}@b1.local",
            name="b1-user",
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
                role=BuiltinRole.MEMBER.value,
                status=MembershipStatus.ACTIVE,
            )
        )

        col = await AsyncRepository(db, KnowledgeCollection).create(
            workspace_id=ws.id,
            name="b1-col",
            description="B1 test collection",
            config_json={},
            created_by=user.id,
        )
        await db.commit()

        return {
            "ws_id": str(ws.id),
            "user_id": str(user.id),
            "col_id": str(col.id),
        }


async def _write_attachment(
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    mime: str,
    data: bytes,
    kind: AttachmentKind,
) -> uuid.UUID:
    """Skip the full HTTP upload path and write the blob + row directly."""
    from app.services import attachment as att_svc  # noqa: PLC0415

    factory = get_session_factory()
    async with factory() as db:
        att = await att_svc.store_bytes(
            db,
            workspace_id=workspace_id,
            uploader_identity_id=user_id,
            filename=filename,
            mime_type=mime,
            data=data,
            session_id=None,
        )
        await db.commit()
        # Storage helper classifies textual mimes correctly; override kind if
        # the caller seeded something unusual (e.g. audio with bogus bytes).
        if att.kind != kind:
            await db.refresh(att)
            att.kind = kind
            await db.flush([att])
            await db.commit()
        return att.id


def tok(identity_id: str, workspace_id: str) -> str:
    t, _exp, _jti = create_access_token(
        identity_id=identity_id, workspace_id=workspace_id, extra={}
    )
    return t


async def main() -> None:
    ctx = await setup()
    ws_id = ctx["ws_id"]
    headers = {
        "Authorization": f"Bearer {tok(ctx['user_id'], ws_id)}",
        "X-Workspace-Id": ws_id,
    }

    md_id = await _write_attachment(
        workspace_id=uuid.UUID(ws_id),
        user_id=uuid.UUID(ctx["user_id"]),
        filename="runbook.md",
        mime="text/markdown",
        data=DOC_TEXT.encode("utf-8"),
        kind=AttachmentKind.DOCUMENT,
    )
    audio_id = await _write_attachment(
        workspace_id=uuid.UUID(ws_id),
        user_id=uuid.UUID(ctx["user_id"]),
        filename="clip.mp3",
        mime="audio/mpeg",
        data=b"\x00" * 512,
        kind=AttachmentKind.AUDIO,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1) Import the markdown attachment.
        r = await client.post(
            f"/api/v1/knowledge/collections/{ctx['col_id']}/ingest_attachment",
            json={"attachment_id": str(md_id)},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["source_kind"] == "file", body
        assert body["status"] == "ready", body
        assert body["chunk_count"] >= 1, body
        md = body["metadata_json"]
        assert md.get("attachment_id") == str(md_id), md
        assert md.get("filename") == "runbook.md", md
        print(
            f"  [step1] md → doc.status=ready chunks={body['chunk_count']} "
            f"meta.attachment_id set  (PASS)"
        )

        # 2) Audio attachment → 415 unsupported_kind.
        r2 = await client.post(
            f"/api/v1/knowledge/collections/{ctx['col_id']}/ingest_attachment",
            json={"attachment_id": str(audio_id)},
            headers=headers,
        )
        assert r2.status_code == 415, r2.text
        detail = r2.json().get("detail") or {}
        assert detail.get("code") == "unsupported_kind", detail
        print("  [step2] audio → 415 unsupported_kind  (PASS)")

        # 3) Bogus attachment id → 404.
        r3 = await client.post(
            f"/api/v1/knowledge/collections/{ctx['col_id']}/ingest_attachment",
            json={"attachment_id": str(uuid.uuid4())},
            headers=headers,
        )
        assert r3.status_code == 404, r3.text
        print("  [step3] bogus id → 404  (PASS)")

    print("\n[PASS] B1 attachment → knowledge ingest verification complete")


if __name__ == "__main__":
    asyncio.run(main())
