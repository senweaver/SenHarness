"""D6 E2E — verify approval permission matrix end-to-end.

Scenario:
  - Create workspace W with two memberships: alice=admin, bob=member, carol=member.
  - Bob owns session S; runner triggers an approval row.
  - Carol (member, not session owner) → require_decide_approval raises 403.
  - Bob (member, session owner) → allowed via approvals.decide_own.
  - Alice (admin) → allowed via approvals.decide_all.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

logging.basicConfig(level=logging.WARNING)

from app.core.errors import PermissionDenied
from app.core.security import utcnow_naive
from app.db.session import get_session_factory
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.session import Session, SessionKind
from app.db.models.workspace import Workspace, WorkspacePlan, DEFAULT_BRANDING
from app.db.models.role import BuiltinRole
from app.services import permissions as perm


async def main() -> None:
    factory = get_session_factory()
    async with factory() as db:
        # 1) Provision actors.
        ws = Workspace(
            slug=f"perm-test-{uuid.uuid4().hex[:6]}",
            name="Perm Test",
            plan=WorkspacePlan.FREE,
            branding_json={**DEFAULT_BRANDING},
            home_config_json={},
            quota_json={},
        )
        db.add(ws)
        await db.flush()

        def make_id(label: str) -> Identity:
            return Identity(
                email=f"{label}-{uuid.uuid4().hex[:6]}@perm-test.local",
                name=label,
                password_hash="$2b$10$dummy",
                status=IdentityStatus.ACTIVE,
                platform_role=PlatformRole.USER,
                profile_json={},
            )

        alice = make_id("alice"); bob = make_id("bob"); carol = make_id("carol")
        db.add_all([alice, bob, carol])
        await db.flush()

        for ident, role in (
            (alice, BuiltinRole.ADMIN.value),
            (bob, BuiltinRole.MEMBER.value),
            (carol, BuiltinRole.MEMBER.value),
        ):
            db.add(
                Membership(
                    workspace_id=ws.id,
                    identity_id=ident.id,
                    role=role,
                    status=MembershipStatus.ACTIVE,
                )
            )
        await db.flush()

        # 2) Bob owns a session that triggered an approval.
        sess = Session(
            workspace_id=ws.id,
            kind=SessionKind.P2P,
            owner_identity_id=bob.id,
            title="Bob's chat",
            metadata_json={},
        )
        db.add(sess)
        await db.flush()

        approval = Approval(
            workspace_id=ws.id,
            session_id=sess.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="execute",
            tool_args={"command": "echo perm"},
            summary="$ echo perm",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=bob.id,
            expires_at=utcnow_naive(),
        )
        db.add(approval)
        await db.commit()
        await db.refresh(approval)

        # 3) Look up memberships.
        from app.repositories.workspace import MembershipRepository

        repo = MembershipRepository(db)
        m_alice = await repo.get_by_identity_and_workspace(alice.id, ws.id)
        m_bob = await repo.get_by_identity_and_workspace(bob.id, ws.id)
        m_carol = await repo.get_by_identity_and_workspace(carol.id, ws.id)

        # 4) Run the matrix.
        results: list[tuple[str, bool, str]] = []
        for label, mem in [("alice/admin", m_alice), ("bob/owner-member", m_bob), ("carol/random-member", m_carol)]:
            try:
                rule = await perm.require_decide_approval(
                    db, approval=approval, actor_membership=mem
                )
                results.append((label, True, rule))
            except PermissionDenied as e:
                results.append((label, False, e.code))

        print("\n=== Permission matrix ===")
        for label, allowed, info in results:
            mark = "✓" if allowed else "✕"
            print(f"  {mark} {label:25s} → {info}")

        # Visibility check
        print("\n=== Visibility ===")
        for label, mem in [("alice", m_alice), ("bob", m_bob), ("carol", m_carol)]:
            visible = await perm.evaluate_approval_visibility(
                db, approval=approval, actor_membership=mem
            )
            print(f"  {('✓' if visible else '✕')} {label:25s}")

        # Capability shortcuts
        print("\n=== capabilities_for snapshot ===")
        for r in ("owner", "admin", "operator", "member", "auditor", "guest"):
            caps = sorted(perm.capabilities_for(r))
            print(f"  {r:9s}: {caps}")


if __name__ == "__main__":
    asyncio.run(main())
