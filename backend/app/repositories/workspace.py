"""Workspace + membership + role + department + invitation repositories."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.db.models.department import Department
from app.db.models.identity import Identity
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership
from app.db.models.role import Role
from app.db.models.workspace import Workspace
from app.db.repository import AsyncRepository

# Reserved slugs that the workspace switcher / listing UI must hide. The
# system tenant exists to host vendored built-in agent templates and
# never gets human members, so nobody should ever see it.
HIDDEN_WORKSPACE_SLUGS: frozenset[str] = frozenset({"senharness-system"})


class WorkspaceRepository(AsyncRepository[Workspace]):
    model = Workspace

    async def get_by_slug(self, slug: str) -> Workspace | None:
        return await self.get_by(slug=slug)


class MembershipRepository(AsyncRepository[Membership]):
    model = Membership

    async def get_by_identity_and_workspace(
        self, identity_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> Membership | None:
        return await self.get_by(identity_id=identity_id, workspace_id=workspace_id)

    async def list_with_workspace_for_identity(
        self,
        identity_id: uuid.UUID,
        *,
        include_system: bool = False,
    ) -> list[tuple[Membership, Workspace]]:
        stmt = (
            select(Membership, Workspace)
            .join(Workspace, Workspace.id == Membership.workspace_id)
            .where(
                Membership.identity_id == identity_id,
                Membership.deleted_at.is_(None),
                Workspace.deleted_at.is_(None),
            )
        )
        if not include_system and HIDDEN_WORKSPACE_SLUGS:
            stmt = stmt.where(Workspace.slug.notin_(HIDDEN_WORKSPACE_SLUGS))
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def list_with_identity(
        self, *, workspace_id: uuid.UUID, limit: int = 500
    ) -> list[tuple[Membership, Identity]]:
        """Return all members joined with their identity (name / email / avatar)."""
        stmt = (
            select(Membership, Identity)
            .join(Identity, Identity.id == Membership.identity_id)
            .where(
                Membership.workspace_id == workspace_id,
                Membership.deleted_at.is_(None),
            )
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def count_by_department(
        self, *, workspace_id: uuid.UUID
    ) -> dict[uuid.UUID, int]:
        """Count active members per department (skips NULL)."""
        from sqlalchemy import func

        stmt = (
            select(Membership.department_id, func.count(Membership.id))
            .where(
                Membership.workspace_id == workspace_id,
                Membership.deleted_at.is_(None),
                Membership.department_id.is_not(None),
            )
            .group_by(Membership.department_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {row[0]: int(row[1] or 0) for row in rows}


class RoleRepository(AsyncRepository[Role]):
    model = Role


class DepartmentRepository(AsyncRepository[Department]):
    model = Department


class InvitationRepository(AsyncRepository[Invitation]):
    model = Invitation

    async def get_by_code(self, code: str) -> Invitation | None:
        return await self.get_by(code=code)
