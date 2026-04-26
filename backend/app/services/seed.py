"""Seed defaults: demo workspace + default Agent '{app_name} 助手'."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import WorkspaceRepository
from app.services import agent as agent_svc
from app.services import workspace as ws_svc


async def seed_defaults(session: AsyncSession) -> list[str]:
    """Idempotent. Returns a human-readable summary of what was created."""
    log: list[str] = []

    # 1) Demo identity if missing
    ident_repo = IdentityRepository(session)
    demo_email = "demo@senharness.app"
    identity = await ident_repo.get_by_email(demo_email)
    if identity is None:
        identity = await ident_repo.create(
            email=demo_email,
            name="Demo User",
            password_hash=hash_password("senharness"),
            status=IdentityStatus.ACTIVE,
            platform_role=PlatformRole.USER,
        )
        log.append(f"[green]+ Identity[/green] {demo_email} (password=senharness)")
    else:
        log.append(f"[cyan]= Identity[/cyan] {demo_email}")

    # 2) Demo workspace if missing
    ws_repo = WorkspaceRepository(session)
    workspace = await ws_repo.get_by_slug("demo")
    workspace_was_created = workspace is None
    if workspace is None:
        # ``create_workspace`` already plants the default agent + owner
        # membership, so steps 2b/3 below see them as "existing" on a fresh
        # bootstrap.
        workspace = await ws_svc.create_workspace(
            session,
            name="Demo Workspace",
            slug="demo",
            owner_identity_id=identity.id,
            description="Seeded by `make seed`",
        )
        log.append(f"[green]+ Workspace[/green] {workspace.slug}")
    else:
        log.append(f"[cyan]= Workspace[/cyan] {workspace.slug}")

    # 2b) Ensure demo identity is a member of the workspace (idempotent).
    from app.db.models.membership import MembershipStatus
    from app.db.models.role import BuiltinRole
    from app.repositories.workspace import MembershipRepository

    mem_repo = MembershipRepository(session)
    membership = await mem_repo.get_by_identity_and_workspace(identity.id, workspace.id)
    if membership is None:
        await mem_repo.create(
            workspace_id=workspace.id,
            identity_id=identity.id,
            role=BuiltinRole.OWNER.value,
            status=MembershipStatus.ACTIVE,
        )
        log.append(f"[green]+ Membership[/green] {demo_email} -> {workspace.slug} (owner)")

    # 3) Default Agent — delegated to agent_svc so create_workspace and the
    #    alembic backfill use the exact same definition.
    name = agent_svc.default_agent_name()
    await agent_svc.ensure_default_agent(
        session, workspace_id=workspace.id, created_by=identity.id
    )
    log.append(
        f"[green]+ Agent[/green] {name}" if workspace_was_created
        else f"[cyan]= Agent[/cyan] {name}"
    )

    _ = Identity  # keep import for type checkers
    return log
