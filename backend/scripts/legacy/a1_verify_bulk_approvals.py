"""A1 verification — bulk approve / deny endpoint.

Checkpoints:

1. Seed 3 pending approvals + 1 already-approved row.
2. ``POST /approvals/bulk-decision`` with all 4 ids + a bogus id ⇒
   ``succeeded == 3`` (the fresh pending ones), ``failed`` contains the
   ``already_decided`` row and the ``not_found`` one.
3. A non-admin member who is NOT the session owner gets ``no_permission``
   for rows they didn't create (regression for the row-level auth check).
4. A second bulk call over the now-decided rows returns all ``already_decided``.

Run with:  ``python -m scripts.a1_verify_bulk_approvals``
"""
from __future__ import annotations

import asyncio
import logging
import uuid

logging.basicConfig(level=logging.WARNING)

import httpx

from app.core.security import create_access_token, hash_password
from app.db.models.approval import Approval, ApprovalStatus
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
            slug=f"a1-test-{uuid.uuid4().hex[:6]}",
            name="A1 Test",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add(ws)
        await db.flush()

        def make(label: str) -> Identity:
            return Identity(
                email=f"{label}-{uuid.uuid4().hex[:6]}@a1.local",
                name=label,
                password_hash=hash_password("x" * 12),
                status=IdentityStatus.ACTIVE,
                platform_role=PlatformRole.USER,
                profile_json={},
            )

        admin = make("admin")
        member = make("member")
        db.add_all([admin, member])
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
                    identity_id=member.id,
                    role=BuiltinRole.MEMBER.value,
                    status=MembershipStatus.ACTIVE,
                ),
            ]
        )
        sess = Session(
            workspace_id=ws.id,
            kind=SessionKind.P2P,
            owner_identity_id=admin.id,
            title="a1 smoke",
            metadata_json={},
        )
        db.add(sess)
        await db.flush()

        # 3 pending rows (by admin) + 1 already-approved row.
        ids: list[uuid.UUID] = []
        for i in range(3):
            ap = Approval(
                workspace_id=ws.id,
                session_id=sess.id,
                agent_id=None,
                run_id=uuid.uuid4(),
                tool_name="execute",
                tool_args={"command": f"echo a1-{i}"},
                summary=f"$ echo a1-{i}",
                status=ApprovalStatus.PENDING,
                requested_by_identity_id=admin.id,
                expires_at=None,
            )
            db.add(ap)
            await db.flush()
            ids.append(ap.id)
        already = Approval(
            workspace_id=ws.id,
            session_id=sess.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="execute",
            tool_args={"command": "echo already"},
            summary="$ echo already",
            status=ApprovalStatus.APPROVED,
            requested_by_identity_id=admin.id,
            expires_at=None,
        )
        db.add(already)
        await db.flush()
        ids.append(already.id)
        await db.commit()

        return {
            "workspace_id": str(ws.id),
            "admin_id": str(admin.id),
            "member_id": str(member.id),
            "pending_ids": [str(i) for i in ids[:3]],
            "already_id": str(ids[3]),
        }


def tok(identity_id: str, workspace_id: str) -> str:
    t, _exp, _jti = create_access_token(
        identity_id=identity_id, workspace_id=workspace_id, extra={}
    )
    return t


async def _bulk(
    client: httpx.AsyncClient,
    token: str,
    ws_id: str,
    ids: list[str],
    action: str = "approve",
    reason: str | None = None,
) -> dict:
    r = await client.post(
        "/api/v1/approvals/bulk-decision",
        json={"approval_ids": ids, "action": action, "reason": reason},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Workspace-Id": ws_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


async def main() -> None:
    ctx = await setup()
    ws_id = ctx["workspace_id"]
    admin_tok = tok(ctx["admin_id"], ws_id)
    member_tok = tok(ctx["member_id"], ws_id)
    pending = ctx["pending_ids"]
    already = ctx["already_id"]
    bogus = str(uuid.uuid4())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1) Admin bulk-approves pending + already-approved + bogus.
        res = await _bulk(
            client, admin_tok, ws_id, pending + [already, bogus], action="approve"
        )
        assert sorted(res["succeeded"]) == sorted(pending), res
        codes = sorted([f["error_code"] for f in res["failed"]])
        assert codes == sorted(["already_decided", "not_found"]), codes
        print(
            f"  [step1] admin bulk approve: ok={len(res['succeeded'])} "
            f"fail codes={codes}  (PASS)"
        )

        # 2) Second call over the same ids → everything already_decided.
        res2 = await _bulk(client, admin_tok, ws_id, pending, action="approve")
        assert res2["succeeded"] == [], res2
        assert all(f["error_code"] == "already_decided" for f in res2["failed"]), res2
        print(
            f"  [step2] idempotent: all {len(res2['failed'])} rows already_decided  (PASS)"
        )

        # 3) Seed another pending (by admin) then try a plain member →
        # should come back as no_permission (member is not the requester,
        # not the session owner, and has only member role).
        factory = get_session_factory()
        async with factory() as db:
            from sqlalchemy import select

            sess = (
                await db.execute(
                    select(Session).where(Session.workspace_id == uuid.UUID(ws_id))
                )
            ).scalar_one()
            fresh = Approval(
                workspace_id=uuid.UUID(ws_id),
                session_id=sess.id,
                agent_id=None,
                run_id=uuid.uuid4(),
                tool_name="execute",
                tool_args={"command": "echo member-attempt"},
                summary="$ echo member-attempt",
                status=ApprovalStatus.PENDING,
                requested_by_identity_id=uuid.UUID(ctx["admin_id"]),
                expires_at=None,
            )
            db.add(fresh)
            await db.commit()
            await db.refresh(fresh)
            fresh_id = str(fresh.id)

        res3 = await _bulk(client, member_tok, ws_id, [fresh_id], action="deny", reason="nope")
        assert res3["succeeded"] == [], res3
        assert res3["failed"] and res3["failed"][0]["error_code"] == "no_permission", res3
        print("  [step3] plain member → no_permission  (PASS)")

        # 4) Admin can still decide that fresh row.
        res4 = await _bulk(client, admin_tok, ws_id, [fresh_id], action="deny", reason="cleanup")
        assert res4["succeeded"] == [fresh_id], res4
        print("  [step4] admin decides the member-blocked row  (PASS)")

    print("\n[PASS] A1 bulk-decision verification complete")


if __name__ == "__main__":
    asyncio.run(main())
