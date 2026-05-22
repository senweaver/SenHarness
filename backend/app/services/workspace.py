"""Workspace + member + invitation service."""

from __future__ import annotations

import uuid
from datetime import timedelta

from slugify import slugify
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied
from app.core.security import random_token, utcnow_naive
from app.db.models.invitation import Invitation
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.role import BuiltinRole
from app.db.models.workspace import DEFAULT_BRANDING, Workspace
from app.repositories.workspace import (
    InvitationRepository,
    MembershipRepository,
    WorkspaceRepository,
)


async def create_workspace(
    session: AsyncSession,
    *,
    name: str,
    slug: str | None,
    owner_identity_id: uuid.UUID,
    description: str | None = None,
) -> Workspace:
    # Local import to dodge any future circulars between agent ↔ workspace
    # services. `agent` already imports nothing from this module.
    from app.services import agent as agent_svc

    ws_repo = WorkspaceRepository(session)
    final_slug = slug or slugify(name)[:64] or uuid.uuid4().hex[:12]
    if await ws_repo.get_by_slug(final_slug):
        raise Conflict("workspace_slug_taken", code="workspace.slug_taken")

    workspace = await ws_repo.create(
        name=name,
        slug=final_slug,
        description=description,
        branding_json={**DEFAULT_BRANDING},
        home_config_json={},
        quota_json={},
    )

    # Owner membership
    mem_repo = MembershipRepository(session)
    await mem_repo.create(
        workspace_id=workspace.id,
        identity_id=owner_identity_id,
        role=BuiltinRole.OWNER.value,
        status=MembershipStatus.ACTIVE,
    )

    # Plant a default agent so first-message dispatch never lands on
    # ``session.no_agent``. Idempotent — re-running with the same workspace
    # is a no-op (used by alembic backfill + seed).
    await agent_svc.ensure_default_agent(
        session,
        workspace_id=workspace.id,
        created_by=owner_identity_id,
    )

    return workspace


async def ensure_member_access(
    session: AsyncSession, *, workspace_id: uuid.UUID, identity_id: uuid.UUID
) -> Membership:
    mem = await MembershipRepository(session).get_by_identity_and_workspace(
        identity_id, workspace_id
    )
    if mem is None or mem.status != MembershipStatus.ACTIVE:
        raise PermissionDenied("not_a_member", code="workspace.not_a_member")
    return mem


async def list_active_workspace_ids_for_identity(
    session: AsyncSession,
    *,
    identity_id: uuid.UUID,
    limit: int = 50,
) -> list[uuid.UUID]:
    """Workspace ids the identity has ACTIVE membership in.

    Capped at ``limit`` (alphabetical by name) so callers that fan a
    cross-workspace query stay O(small) even for identities with
    pathological membership counts. Hidden / deleted workspaces are
    filtered out via :meth:`MembershipRepository.list_with_workspace_for_identity`.
    """
    pairs = await MembershipRepository(session).list_with_workspace_for_identity(identity_id)
    active_pairs = [(mem, ws) for mem, ws in pairs if mem.status == MembershipStatus.ACTIVE]
    active_pairs.sort(key=lambda pair: (pair[1].name or "").lower())
    return [ws.id for _, ws in active_pairs[:limit]]


async def ensure_admin(
    session: AsyncSession, *, workspace_id: uuid.UUID, identity_id: uuid.UUID
) -> Membership:
    mem = await ensure_member_access(session, workspace_id=workspace_id, identity_id=identity_id)
    if mem.role not in {BuiltinRole.OWNER.value, BuiltinRole.ADMIN.value}:
        raise PermissionDenied("admin_required", code="workspace.admin_required")
    return mem


# ─── Invitations ─────────────────────────────────────────
async def create_invitation(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    invited_by: uuid.UUID,
    email: str | None,
    role: str = BuiltinRole.MEMBER.value,
    department_id: uuid.UUID | None = None,
    expires_in_hours: int = 72,
) -> Invitation:
    code = random_token(24)
    expires = utcnow_naive() + timedelta(hours=expires_in_hours)
    return await InvitationRepository(session).create(
        workspace_id=workspace_id,
        code=code,
        email=email,
        role=role,
        department_id=department_id,
        expires_at=expires,
        invited_by=invited_by,
    )


async def accept_invitation(
    session: AsyncSession, *, code: str, identity_id: uuid.UUID
) -> Membership:
    from app.services import stars as stars_svc

    inv_repo = InvitationRepository(session)
    inv = await inv_repo.get_by_code(code)
    if inv is None or inv.used_at is not None:
        raise NotFound("invitation_not_found", code="invitation.not_found")
    if inv.expires_at and inv.expires_at < utcnow_naive():
        raise PermissionDenied("invitation_expired", code="invitation.expired")

    mem_repo = MembershipRepository(session)
    existing = await mem_repo.get_by_identity_and_workspace(identity_id, inv.workspace_id)
    if existing is not None:
        if existing.status != MembershipStatus.ACTIVE:
            await mem_repo.update(existing, status=MembershipStatus.ACTIVE, role=inv.role)
        membership = existing
    else:
        membership = await mem_repo.create(
            workspace_id=inv.workspace_id,
            identity_id=identity_id,
            role=inv.role,
            department_id=inv.department_id,
            status=MembershipStatus.ACTIVE,
            invited_by=inv.invited_by,
        )

    await session.flush()
    await stars_svc.fan_out_workspace_to_member(
        session, workspace_id=inv.workspace_id, identity_id=identity_id
    )

    await inv_repo.update(inv, used_at=utcnow_naive())
    return membership
