"""Internal-ops SQLAdmin mount (B3).

Mounted at ``/admin/sql/`` — separate from the React-based ``/admin`` page so
the two can coexist. Auth is form-based (email + password) tied to the
``identities`` table; the only successful login path is for accounts with
``platform_role == platform_admin``.

Read-only by default — admin views below set ``can_create / can_edit /
can_delete = False`` so the panel is a *browser* rather than a god mode. Need
to actually mutate something? Use the proper REST endpoints with audit
trails.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.core.config import settings
from app.core.security import verify_password
from app.db.models.agent import Agent
from app.db.models.approval import Approval
from app.db.models.attachment import Attachment
from app.db.models.audit import AuditEvent
from app.db.models.channel import Channel
from app.db.models.flow import Flow, FlowRun
from app.db.models.identity import Identity, PlatformRole
from app.db.models.knowledge import KnowledgeCollection, KnowledgeDoc
from app.db.models.membership import Membership
from app.db.models.session import Session as SessionModel
from app.db.models.workspace import Workspace
from app.db.session import get_engine, get_session_factory

log = logging.getLogger(__name__)


def setup_admin(app: FastAPI) -> None:
    """Mount SQLAdmin if the optional dep is installed."""
    try:
        from sqladmin import Admin, ModelView
        from sqladmin.authentication import AuthenticationBackend
        from starlette.requests import Request
    except ImportError:
        log.info("sqladmin not installed; skipping internal admin mount")
        return None

    from sqlalchemy import select

    class JwtAuth(AuthenticationBackend):
        """Form-based auth that checks ``identities`` for a platform admin.

        We deliberately do **not** reuse the JWT cookie here — SQLAdmin uses
        Starlette ``SessionMiddleware`` for its own state, so coupling to
        the API auth would just confuse session lifecycles. The login form
        is the single surface; logout clears the SQLAdmin session.
        """

        async def login(self, request: Request) -> bool:
            form = await request.form()
            email = (form.get("username") or "").strip()
            password = form.get("password") or ""
            if not email or not password:
                return False
            factory = get_session_factory()
            async with factory() as db:
                row = (
                    await db.execute(select(Identity).where(Identity.email == email))
                ).scalar_one_or_none()
                if row is None:
                    return False
                if not verify_password(password, row.password_hash or ""):
                    return False
                if row.platform_role != PlatformRole.PLATFORM_ADMIN:
                    return False
            request.session["sqladmin_user"] = str(row.id)
            request.session["sqladmin_email"] = row.email
            return True

        async def logout(self, request: Request) -> bool:
            request.session.clear()
            return True

        async def authenticate(self, request: Request) -> bool:
            return bool(request.session.get("sqladmin_user"))

    backend = JwtAuth(secret_key=settings.JWT_SECRET_KEY)
    admin = Admin(
        app,
        engine=get_engine(),
        authentication_backend=backend,
        title="SenHarness DB",
        base_url="/admin/sql",
    )

    # ─── Read-only model views ────────────────────────────
    # Helper factory to keep the per-model boilerplate small. Setting all
    # mutation flags to False makes the panel a strict browser.
    def _readonly(
        model,  # type: ignore[no-untyped-def]
        *,
        name: str,
        icon: str,
        cols: list,
        searchable: list | None = None,
        sortable: list | None = None,
    ):
        attrs: dict = {
            "name": name,
            "name_plural": name,
            "icon": icon,
            "column_list": cols,
            "column_searchable_list": searchable or [],
            "column_sortable_list": sortable or [],
            "can_create": False,
            "can_edit": False,
            "can_delete": False,
            "can_export": True,
            "page_size": 50,
        }
        return type(f"{model.__name__}Admin", (ModelView,), attrs, model=model)

    admin.add_view(
        _readonly(
            Identity,
            name="Identity",
            icon="fa-solid fa-user",
            cols=[
                Identity.id,
                Identity.email,
                Identity.name,
                Identity.status,
                Identity.platform_role,
                Identity.created_at,
            ],
            searchable=[Identity.email, Identity.name],
            sortable=[Identity.created_at, Identity.email],
        )
    )
    admin.add_view(
        _readonly(
            Workspace,
            name="Workspace",
            icon="fa-solid fa-building",
            cols=[
                Workspace.id,
                Workspace.slug,
                Workspace.name,
                Workspace.plan,
                Workspace.created_at,
                Workspace.deleted_at,
            ],
            searchable=[Workspace.slug, Workspace.name],
            sortable=[Workspace.created_at],
        )
    )
    admin.add_view(
        _readonly(
            Membership,
            name="Membership",
            icon="fa-solid fa-link",
            cols=[
                Membership.id,
                Membership.workspace_id,
                Membership.identity_id,
                Membership.role,
                Membership.status,
                Membership.created_at,
            ],
        )
    )
    admin.add_view(
        _readonly(
            Agent,
            name="Agent",
            icon="fa-solid fa-robot",
            cols=[
                Agent.id,
                Agent.workspace_id,
                Agent.name,
                Agent.backend_kind,
                Agent.visibility,
                Agent.autonomy_level,
                Agent.created_at,
            ],
            searchable=[Agent.name],
        )
    )
    admin.add_view(
        _readonly(
            SessionModel,
            name="Session",
            icon="fa-solid fa-comments",
            cols=[
                SessionModel.id,
                SessionModel.workspace_id,
                SessionModel.kind,
                SessionModel.title,
                SessionModel.message_count,
                SessionModel.last_message_at,
                SessionModel.created_at,
            ],
            searchable=[SessionModel.title],
            sortable=[SessionModel.last_message_at, SessionModel.created_at],
        )
    )
    admin.add_view(
        _readonly(
            Approval,
            name="Approval",
            icon="fa-solid fa-shield-halved",
            cols=[
                Approval.id,
                Approval.workspace_id,
                Approval.tool_name,
                Approval.status,
                Approval.requested_by_identity_id,
                Approval.decided_at,
                Approval.created_at,
            ],
            searchable=[Approval.tool_name],
        )
    )
    admin.add_view(
        _readonly(
            AuditEvent,
            name="Audit event",
            icon="fa-solid fa-list-check",
            cols=[
                AuditEvent.id,
                AuditEvent.workspace_id,
                AuditEvent.actor_identity_id,
                AuditEvent.action,
                AuditEvent.summary,
                AuditEvent.created_at,
            ],
            searchable=[AuditEvent.action, AuditEvent.summary],
        )
    )
    admin.add_view(
        _readonly(
            Attachment,
            name="Attachment",
            icon="fa-solid fa-paperclip",
            cols=[
                Attachment.id,
                Attachment.workspace_id,
                Attachment.filename,
                Attachment.mime_type,
                Attachment.size_bytes,
                Attachment.kind,
                Attachment.deleted_at,
                Attachment.created_at,
            ],
            searchable=[Attachment.filename],
        )
    )
    admin.add_view(
        _readonly(
            Channel,
            name="Channel",
            icon="fa-solid fa-tower-broadcast",
            cols=[
                Channel.id,
                Channel.workspace_id,
                Channel.name,
                Channel.kind,
                Channel.created_at,
            ],
        )
    )
    admin.add_view(
        _readonly(
            Flow,
            name="Flow",
            icon="fa-solid fa-diagram-project",
            cols=[
                Flow.id,
                Flow.workspace_id,
                Flow.name,
                Flow.trigger_kind,
                Flow.enabled,
                Flow.created_at,
            ],
        )
    )
    admin.add_view(
        _readonly(
            FlowRun,
            name="Flow run",
            icon="fa-solid fa-play",
            cols=[
                FlowRun.id,
                FlowRun.flow_id,
                FlowRun.status,
                FlowRun.started_at,
                FlowRun.finished_at,
            ],
        )
    )
    admin.add_view(
        _readonly(
            KnowledgeCollection,
            name="KB collection",
            icon="fa-solid fa-book",
            cols=[
                KnowledgeCollection.id,
                KnowledgeCollection.workspace_id,
                KnowledgeCollection.name,
                KnowledgeCollection.created_at,
            ],
        )
    )
    admin.add_view(
        _readonly(
            KnowledgeDoc,
            name="KB doc",
            icon="fa-solid fa-file-lines",
            cols=[
                KnowledgeDoc.id,
                KnowledgeDoc.collection_id,
                KnowledgeDoc.title,
                KnowledgeDoc.source_kind,
                KnowledgeDoc.status,
                KnowledgeDoc.chunk_count,
                KnowledgeDoc.created_at,
            ],
            searchable=[KnowledgeDoc.title],
        )
    )

    log.info("SQLAdmin mounted at /admin/sql/ (read-only)")
