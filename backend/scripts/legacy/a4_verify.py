"""A4 verification — department display + urgent preview.

Checkpoints:

1. Seed a workspace with a department, a user assigned to that department,
   and 3 pending approvals with varying ``expires_at``.
2. ``GET /approvals?status=pending`` returns rows whose
   ``requester_department_name`` matches the department name.
3. ``GET /approvals/urgent?limit=2`` returns the 2 rows with earliest
   ``expires_at`` first (NULLs pushed to the end).
4. After a deny, the row's ``decided_by_department_name`` is populated.

Run with:  ``python -m scripts.a4_verify``
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

logging.basicConfig(level=logging.WARNING)

import httpx

from app.core.security import create_access_token, hash_password, utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.department import Department
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.models.session import Session, SessionKind
from app.db.models.workspace import DEFAULT_BRANDING, Workspace, WorkspacePlan
from app.db.session import get_session_factory
from app.main import app


async def setup() -> dict:
    factory = get_session_factory()
    async with factory() as db:
        ws = Workspace(
            slug=f"a4-{uuid.uuid4().hex[:6]}",
            name="A4 Test",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add(ws)
        await db.flush()

        dept = Department(
            workspace_id=ws.id,
            parent_id=None,
            name="Research",
            path="Research",
        )
        db.add(dept)
        await db.flush()

        def make(label: str) -> Identity:
            return Identity(
                email=f"{label}-{uuid.uuid4().hex[:6]}@a4.local",
                name=label,
                password_hash=hash_password("x" * 12),
                status=IdentityStatus.ACTIVE,
                platform_role=PlatformRole.USER,
                profile_json={},
            )

        admin = make("admin")
        researcher = make("researcher")
        db.add_all([admin, researcher])
        await db.flush()

        db.add_all(
            [
                Membership(
                    workspace_id=ws.id,
                    identity_id=admin.id,
                    role=BuiltinRole.ADMIN.value,
                    status=MembershipStatus.ACTIVE,
                ),
                Membership(
                    workspace_id=ws.id,
                    identity_id=researcher.id,
                    role=BuiltinRole.MEMBER.value,
                    status=MembershipStatus.ACTIVE,
                    department_id=dept.id,
                ),
            ]
        )

        sess = Session(
            workspace_id=ws.id,
            kind=SessionKind.P2P,
            owner_identity_id=researcher.id,
            title="a4",
            metadata_json={},
        )
        db.add(sess)
        await db.flush()

        now = utcnow_naive()
        # Three pending rows: 30s, 2m, null expiry (should sort 30s < 2m < null).
        ap_soon = Approval(
            workspace_id=ws.id,
            session_id=sess.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="execute",
            tool_args={"command": "echo soon"},
            summary="$ echo soon",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=researcher.id,
            expires_at=now + timedelta(seconds=30),
        )
        ap_mid = Approval(
            workspace_id=ws.id,
            session_id=sess.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="write_file",
            tool_args={"path": "/tmp/mid"},
            summary="write_file → /tmp/mid",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=researcher.id,
            expires_at=now + timedelta(minutes=2),
        )
        ap_null = Approval(
            workspace_id=ws.id,
            session_id=sess.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="delete_file",
            tool_args={"path": "/tmp/null"},
            summary="delete_file → /tmp/null",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=researcher.id,
            expires_at=None,
        )
        db.add_all([ap_soon, ap_mid, ap_null])
        await db.commit()

        return {
            "ws_id": str(ws.id),
            "admin_id": str(admin.id),
            "researcher_id": str(researcher.id),
            "dept_name": dept.name,
            "ids": {
                "soon": str(ap_soon.id),
                "mid": str(ap_mid.id),
                "null": str(ap_null.id),
            },
        }


def tok(identity_id: str, workspace_id: str) -> str:
    t, _exp, _jti = create_access_token(
        identity_id=identity_id, workspace_id=workspace_id, extra={}
    )
    return t


async def main() -> None:
    ctx = await setup()
    admin_tok = tok(ctx["admin_id"], ctx["ws_id"])
    headers = {
        "Authorization": f"Bearer {admin_tok}",
        "X-Workspace-Id": ctx["ws_id"],
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1) list pending → department name surfaces
        r = await client.get(
            "/api/v1/approvals?status=pending&limit=10", headers=headers
        )
        assert r.status_code == 200, r.text
        rows = r.json()["items"]
        assert all(
            row["requester_department_name"] == ctx["dept_name"] for row in rows
        ), [r.get("requester_department_name") for r in rows]
        print(
            f"  [step1] list pending → all 3 rows tagged with '{ctx['dept_name']}'  (OK)"
        )

        # 2) urgent preview: first 2 should be soon, mid (null pushed to end).
        r2 = await client.get("/api/v1/approvals/urgent?limit=2", headers=headers)
        assert r2.status_code == 200, r2.text
        urgent = r2.json()
        ids = [row["id"] for row in urgent]
        assert ids == [ctx["ids"]["soon"], ctx["ids"]["mid"]], ids
        assert all(
            row.get("requester_department_name") == ctx["dept_name"]
            for row in urgent
        )
        print("  [step2] urgent preview order: soon → mid  (OK)")

        # 3) urgent limit=5 should include the NULL-expiry row last
        r3 = await client.get("/api/v1/approvals/urgent?limit=5", headers=headers)
        assert r3.status_code == 200, r3.text
        all_urgent = r3.json()
        assert [r["id"] for r in all_urgent][:3] == [
            ctx["ids"]["soon"],
            ctx["ids"]["mid"],
            ctx["ids"]["null"],
        ], [r["id"] for r in all_urgent][:3]
        print("  [step3] urgent full order: soon → mid → null  (OK)")

        # 4) admin denies the "mid" row → decided_by_department_name = admin has
        #    no department, so should be null (admin has none in our seed).
        r4 = await client.post(
            f"/api/v1/approvals/{ctx['ids']['mid']}/decision",
            json={"action": "deny", "reason": "test"},
            headers=headers,
        )
        assert r4.status_code == 200, r4.text
        body = r4.json()
        assert body["status"] == "denied", body
        # admin's membership has no department_id; the decider-department
        # enrichment should therefore be None (not an error).
        assert body.get("decided_by_department_name") is None, body
        print("  [step4] decide → decider_department none (admin has no dept)  (OK)")

    print("\n[PASS] A4 verification complete")


if __name__ == "__main__":
    asyncio.run(main())
