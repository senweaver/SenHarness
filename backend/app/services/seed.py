"""Seed defaults: demo workspace + default Agent '{app_name} 助手'."""

from __future__ import annotations

import secrets
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.repositories.identity import IdentityRepository
from app.repositories.workspace import WorkspaceRepository
from app.services import agent as agent_svc
from app.services import workspace as ws_svc

# System workspace + identity that own the vendored agent templates.
# Hidden from the regular workspace switcher (see ``WorkspaceRepository``
# and ``api/v1/workspaces.py``). Never granted any UI session — its
# password hash is random throwaway entropy.
SYSTEM_IDENTITY_EMAIL = "system@senharness"
SYSTEM_WORKSPACE_SLUG = "senharness-system"


async def _ensure_system_identity(session: AsyncSession) -> Identity:
    """Idempotent. The system identity is the ``created_by`` for templates."""
    repo = IdentityRepository(session)
    existing = await repo.get_by_email(SYSTEM_IDENTITY_EMAIL)
    if existing is not None:
        return existing
    return await repo.create(
        email=SYSTEM_IDENTITY_EMAIL,
        name="SenHarness System",
        password_hash=hash_password(secrets.token_urlsafe(32)),
        status=IdentityStatus.ACTIVE,
        platform_role=PlatformRole.USER,
    )


async def _ensure_system_workspace(
    session: AsyncSession, *, owner_identity_id: uuid.UUID
) -> tuple[uuid.UUID, bool]:
    """Idempotent. Returns ``(workspace_id, was_created)``."""
    from app.db.models.workspace import DEFAULT_BRANDING

    ws_repo = WorkspaceRepository(session)
    existing = await ws_repo.get_by_slug(SYSTEM_WORKSPACE_SLUG)
    if existing is not None:
        return existing.id, False
    # Bypass ``ws_svc.create_workspace`` — that helper plants a default
    # agent + an owner membership we don't want for the system tenant.
    ws = await ws_repo.create(
        name="SenHarness System",
        slug=SYSTEM_WORKSPACE_SLUG,
        description="内置智能体模板托管 workspace, 不向普通用户展示。",
        branding_json={**DEFAULT_BRANDING},
        home_config_json={},
        quota_json={},
    )
    _ = owner_identity_id  # not needed yet — kept for symmetry
    return ws.id, True


async def seed_defaults(session: AsyncSession) -> list[str]:
    """Idempotent. Returns a human-readable summary of what was created."""
    log: list[str] = []

    # 0) System identity + workspace + built-in agent templates.
    sys_identity = await _ensure_system_identity(session)
    sys_ws_id, sys_created = await _ensure_system_workspace(
        session, owner_identity_id=sys_identity.id
    )
    log.append(
        f"[green]+ System workspace[/green] {SYSTEM_WORKSPACE_SLUG}"
        if sys_created
        else f"[cyan]= System workspace[/cyan] {SYSTEM_WORKSPACE_SLUG}"
    )

    from app.services.agent_templates import loader as tpl_loader

    created, updated = await tpl_loader.load_all(
        session,
        system_workspace_id=sys_ws_id,
        system_identity_id=sys_identity.id,
    )
    log.append(
        f"[green]+ Templates[/green] {created} 新增 / {updated} 更新 (内置智能体)"
    )

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
