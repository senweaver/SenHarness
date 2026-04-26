"""D6 REST E2E — POST /approvals/{id}/decision should 403 for unauthorized roles.

Bypasses HTTP and exercises the FastAPI app via httpx.AsyncClient + ASGI.
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
            slug=f"d6-test-{uuid.uuid4().hex[:6]}",
            name="D6 Test",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add(ws)
        await db.flush()

        def make(label: str) -> Identity:
            return Identity(
                email=f"{label}-{uuid.uuid4().hex[:6]}@d6.local",
                name=label,
                password_hash=hash_password("x" * 12),
                status=IdentityStatus.ACTIVE,
                platform_role=PlatformRole.USER,
                profile_json={},
            )

        alice = make("alice"); bob = make("bob"); carol = make("carol")
        db.add_all([alice, bob, carol])
        await db.flush()
        for ident, role in (
            (alice, BuiltinRole.ADMIN.value),
            (bob, BuiltinRole.MEMBER.value),
            (carol, BuiltinRole.MEMBER.value),
        ):
            db.add(
                Membership(
                    workspace_id=ws.id, identity_id=ident.id,
                    role=role, status=MembershipStatus.ACTIVE,
                )
            )
        sess = Session(
            workspace_id=ws.id, kind=SessionKind.P2P,
            owner_identity_id=bob.id, title="bob's session", metadata_json={},
        )
        db.add(sess)
        await db.flush()
        approval = Approval(
            workspace_id=ws.id, session_id=sess.id, agent_id=None,
            run_id=uuid.uuid4(), tool_name="execute",
            tool_args={"command": "echo perm"}, summary="$ echo perm",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=bob.id, expires_at=None,
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)
        return {
            "workspace_id": str(ws.id),
            "approval_id": str(approval.id),
            "alice_id": str(alice.id),
            "bob_id": str(bob.id),
            "carol_id": str(carol.id),
        }


def tok(identity_id: str, workspace_id: str) -> str:
    t, _exp, _jti = create_access_token(
        identity_id=identity_id, workspace_id=workspace_id, extra={}
    )
    return t


async def main() -> None:
    ctx = await setup()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for actor, expected in [
            ("carol_id", 403),  # random member, not the requester → denied
            ("alice_id", 200),  # admin → allowed
        ]:
            headers = {
                "Authorization": f"Bearer {tok(ctx[actor], ctx['workspace_id'])}",
                "X-Workspace-Id": ctx["workspace_id"],
            }
            # Use a fresh approval for the second call (alice approves it).
            if actor == "alice_id":
                # carol's call should not have decided it (she got 403),
                # so the row is still pending. Use the same id.
                pass
            r = await client.post(
                f"/api/v1/approvals/{ctx['approval_id']}/decision",
                json={"action": "approve", "reason": f"by {actor}"},
                headers=headers,
            )
            status_label = "PASS" if r.status_code == expected else "FAIL"
            try:
                body = r.json()
            except Exception:
                body = r.text[:120]
            print(
                f"  [{status_label}] {actor:9s} → status={r.status_code} (expected {expected}) body={str(body)[:120]}"
            )


if __name__ == "__main__":
    asyncio.run(main())
