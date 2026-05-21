"""A2 + A3 verification.

A2 — platform-admin cross-workspace approvals:
    1. Seed 2 workspaces + a pending approval in each.
    2. A platform admin hits ``GET /admin/approvals?status=pending`` and sees
       both rows (without needing workspace membership in either).
    3. A normal user gets 403 on the same endpoint.
    4. Admin posts ``/admin/approvals/{id}/decision`` on the cross-tenant row
       → DB row becomes approved with ``decided_by_identity_id = admin``.

A3 — cancel_pending_for_session helper:
    5. Insert two pending approvals in one workspace/session; call the
       repository helper directly (WS path is non-trivial to unit-test); both
       rows move to ``status=cancelled`` with ``decided_reason="session cancelled"``.
    6. Second call on the same session is a no-op (returns empty list).

Run with:  ``python -m scripts.a2_a3_verify``
"""
from __future__ import annotations

import asyncio
import logging
import uuid

logging.basicConfig(level=logging.WARNING)

import httpx

from app.core.security import create_access_token, hash_password, utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.models.session import Session, SessionKind
from app.db.models.workspace import DEFAULT_BRANDING, Workspace, WorkspacePlan
from app.db.session import get_session_factory
from app.main import app
from app.repositories.approval import ApprovalRepository


async def setup_two_workspaces() -> dict:
    factory = get_session_factory()
    async with factory() as db:
        # Two isolated workspaces.
        ws_a = Workspace(
            slug=f"a2-{uuid.uuid4().hex[:6]}",
            name="WS A",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        ws_b = Workspace(
            slug=f"a2-{uuid.uuid4().hex[:6]}",
            name="WS B",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add_all([ws_a, ws_b])
        await db.flush()

        def ident(label: str, role: PlatformRole) -> Identity:
            return Identity(
                email=f"{label}-{uuid.uuid4().hex[:6]}@a2.local",
                name=label,
                password_hash=hash_password("x" * 12),
                status=IdentityStatus.ACTIVE,
                platform_role=role,
                profile_json={},
            )

        platform_admin = ident("platform-admin", PlatformRole.PLATFORM_ADMIN)
        stranger = ident("stranger", PlatformRole.USER)
        # Requester in ws_a only — platform admin does NOT need ws membership.
        requester = ident("requester", PlatformRole.USER)
        db.add_all([platform_admin, stranger, requester])
        await db.flush()

        # Only requester has a membership. platform_admin has none (tests the
        # cross-tenant bypass).
        db.add_all(
            [
                Membership(
                    workspace_id=ws_a.id,
                    identity_id=requester.id,
                    role=BuiltinRole.MEMBER.value,
                    status=MembershipStatus.ACTIVE,
                ),
                Membership(
                    workspace_id=ws_b.id,
                    identity_id=stranger.id,
                    role=BuiltinRole.MEMBER.value,
                    status=MembershipStatus.ACTIVE,
                ),
            ]
        )

        sess_a = Session(
            workspace_id=ws_a.id,
            kind=SessionKind.P2P,
            owner_identity_id=requester.id,
            title="ws a session",
            metadata_json={},
        )
        sess_b = Session(
            workspace_id=ws_b.id,
            kind=SessionKind.P2P,
            owner_identity_id=stranger.id,
            title="ws b session",
            metadata_json={},
        )
        db.add_all([sess_a, sess_b])
        await db.flush()

        ap_a = Approval(
            workspace_id=ws_a.id,
            session_id=sess_a.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="execute",
            tool_args={"command": "echo a"},
            summary="$ echo a",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=requester.id,
            expires_at=None,
        )
        ap_b = Approval(
            workspace_id=ws_b.id,
            session_id=sess_b.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="write_file",
            tool_args={"path": "/tmp/b"},
            summary="write_file → /tmp/b",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=stranger.id,
            expires_at=None,
        )
        db.add_all([ap_a, ap_b])
        await db.commit()

        return {
            "ws_a": str(ws_a.id),
            "ws_b": str(ws_b.id),
            "sess_a": str(sess_a.id),
            "platform_admin": str(platform_admin.id),
            "stranger": str(stranger.id),
            "ap_a": str(ap_a.id),
            "ap_b": str(ap_b.id),
        }


def tok(identity_id: str, workspace_id: str | None = None) -> str:
    t, _exp, _jti = create_access_token(
        identity_id=identity_id, workspace_id=workspace_id, extra={}
    )
    return t


async def verify_a2(ctx: dict) -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        admin_tok = tok(ctx["platform_admin"])  # no ws id → cross-tenant
        stranger_tok = tok(ctx["stranger"], ctx["ws_b"])

        # 1) platform admin sees both pending rows
        r = await client.get(
            "/api/v1/admin/approvals?status=pending",
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert r.status_code == 200, r.text
        rows = r.json()
        ids = {row["id"] for row in rows}
        assert ctx["ap_a"] in ids and ctx["ap_b"] in ids, ids
        ws_ids = {row["workspace_id"] for row in rows}
        assert ctx["ws_a"] in ws_ids and ctx["ws_b"] in ws_ids
        # enrichment:
        for row in rows:
            assert row.get("workspace_name") is not None
            assert row.get("workspace_slug") is not None
        print(f"  [A2.1] admin list → {len(rows)} rows across 2 workspaces  (OK)")

        # 2) stranger → 403
        r2 = await client.get(
            "/api/v1/admin/approvals?status=pending",
            headers={"Authorization": f"Bearer {stranger_tok}"},
        )
        assert r2.status_code == 403, r2.text
        print("  [A2.2] non-admin gets 403  (OK)")

        # 3) admin decides ws_b's approval WITHOUT being a member
        r3 = await client.post(
            f"/api/v1/admin/approvals/{ctx['ap_b']}/decision",
            json={"action": "approve", "reason": "platform cleanup"},
            headers={"Authorization": f"Bearer {admin_tok}"},
        )
        assert r3.status_code == 200, r3.text
        body = r3.json()
        assert body["status"] == "approved", body
        assert body["decided_by_identity_id"] == ctx["platform_admin"], body
        print("  [A2.3] admin bypass decides ws_b row → approved  (OK)")


async def verify_a3(ctx: dict) -> None:
    """Exercise the cancel helper directly; the WS path is tested manually."""
    ws_a = uuid.UUID(ctx["ws_a"])
    sess_a = uuid.UUID(ctx["sess_a"])
    requester = uuid.UUID(ctx["platform_admin"])

    factory = get_session_factory()
    async with factory() as db:
        # Insert two fresh pending rows on sess_a.
        extra_ids: list[uuid.UUID] = []
        for _ in range(2):
            ap = Approval(
                workspace_id=ws_a,
                session_id=sess_a,
                agent_id=None,
                run_id=uuid.uuid4(),
                tool_name="execute",
                tool_args={"command": "echo cancel-me"},
                summary="$ echo cancel-me",
                status=ApprovalStatus.PENDING,
                requested_by_identity_id=requester,
                expires_at=None,
            )
            db.add(ap)
            await db.flush()
            extra_ids.append(ap.id)
        await db.commit()

    # Call the helper in a fresh session (simulates the WS handler path).
    factory = get_session_factory()
    async with factory() as db:
        repo = ApprovalRepository(db)
        cancelled = await repo.cancel_pending_for_session(
            workspace_id=ws_a,
            session_id=sess_a,
            run_id=None,
            decided_by_identity_id=requester,
            reason="session cancelled",
            now=utcnow_naive(),
        )
        await db.commit()
    assert {r.id for r in cancelled} >= set(extra_ids), (cancelled, extra_ids)
    for r in cancelled:
        assert r.status == ApprovalStatus.CANCELLED, r.status
        assert r.decided_reason == "session cancelled"
    print(
        f"  [A3.1] cancel_pending_for_session flipped {len(cancelled)} row(s)  (OK)"
    )

    # Second call is a no-op (nothing pending left).
    factory = get_session_factory()
    async with factory() as db:
        repo = ApprovalRepository(db)
        again = await repo.cancel_pending_for_session(
            workspace_id=ws_a,
            session_id=sess_a,
            run_id=None,
            decided_by_identity_id=requester,
            reason="session cancelled",
            now=utcnow_naive(),
        )
    assert again == [], again
    print("  [A3.2] second call returns empty list (idempotent)  (OK)")


async def main() -> None:
    ctx = await setup_two_workspaces()
    print(f"  [seed] ws_a={ctx['ws_a']}  ws_b={ctx['ws_b']}")
    await verify_a2(ctx)
    await verify_a3(ctx)
    print("\n[PASS] A2 + A3 verification complete")


if __name__ == "__main__":
    asyncio.run(main())
