"""Capability matrix for built-in roles + per-resource gate functions.

A *capability* is a coarse-grained verb the UI also reads to gate widgets:

  workspace.manage           — branding / quotas / billing / providers
  members.manage             — invite / remove / change role / change dept
  agents.manage              — create / edit / delete agents
  squads.manage              — create / edit / delete squads
  secrets.manage             — vault CRUD
  sessions.create            — start a chat
  approvals.view_all         — see workspace-wide pending / history
  approvals.decide_all       — approve or deny ANY approval
  approvals.decide_department— approve or deny if approval's session belongs
                               to my department
  approvals.decide_own       — approve only on sessions I own (started)
  audit.view                 — read-only log access (subset of view_all)

Plus *scope rules* that decide whether an actor can act on a specific
approval row even when they only have ``decide_department`` or
``decide_own``.  These rules live in :func:`can_decide_approval`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDenied
from app.db.models.approval import Approval
from app.db.models.membership import Membership
from app.db.models.role import BuiltinRole
from app.db.models.session import Session

# ──────────────────────────────────────────────────────────────────
# Capability matrix
# ──────────────────────────────────────────────────────────────────
ROLE_CAPABILITIES: dict[str, set[str]] = {
    BuiltinRole.OWNER.value: {
        "workspace.manage",
        "members.manage",
        "agents.manage",
        "squads.manage",
        "secrets.manage",
        "sessions.create",
        "approvals.view_all",
        "approvals.decide_all",
        "approvals.decide_department",
        "approvals.decide_own",
        "audit.view",
    },
    BuiltinRole.ADMIN.value: {
        "workspace.manage",
        "members.manage",
        "agents.manage",
        "squads.manage",
        "secrets.manage",
        "sessions.create",
        "approvals.view_all",
        "approvals.decide_all",
        "approvals.decide_department",
        "approvals.decide_own",
        "audit.view",
    },
    BuiltinRole.OPERATOR.value: {
        "agents.manage",
        "squads.manage",
        "sessions.create",
        "approvals.view_all",
        "approvals.decide_department",
        "approvals.decide_own",
        "audit.view",
    },
    BuiltinRole.MEMBER.value: {
        "sessions.create",
        "approvals.decide_own",
    },
    BuiltinRole.AUDITOR.value: {
        "approvals.view_all",
        "audit.view",
    },
    BuiltinRole.GUEST.value: set(),
}


def capabilities_for(role: str | None) -> set[str]:
    if not role:
        return set()
    return set(ROLE_CAPABILITIES.get(role, set()))


def has_capability(membership: Membership | None, capability: str) -> bool:
    if membership is None:
        return False
    role = membership.role if isinstance(membership.role, str) else getattr(membership.role, "value", None)
    return capability in capabilities_for(role)


# ──────────────────────────────────────────────────────────────────
# Approval-specific scope rules
# ──────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class ApprovalDecisionPermission:
    allowed: bool
    reason: str          # short i18n-able token, "" when allowed
    matched_rule: str    # which rule granted it: all|department|own|none


async def evaluate_approval_decision(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_membership: Membership,
) -> ApprovalDecisionPermission:
    """Decide whether ``actor_membership`` may approve / deny ``approval``."""
    caps = capabilities_for(
        actor_membership.role
        if isinstance(actor_membership.role, str)
        else getattr(actor_membership.role, "value", None)
    )

    if "approvals.decide_all" in caps:
        return ApprovalDecisionPermission(True, "", "all")

    if "approvals.decide_department" in caps:
        # Approval's "owning department" = department of the session owner.
        approval_dept_id = await _session_owner_department_id(
            db, approval.session_id, approval.workspace_id
        )
        if (
            approval_dept_id is not None
            and actor_membership.department_id == approval_dept_id
        ):
            return ApprovalDecisionPermission(True, "", "department")

    if "approvals.decide_own" in caps:
        # Allow when actor is the same identity that triggered the approval
        # (i.e. they own the session that asked for it).
        if approval.requested_by_identity_id is not None and (
            approval.requested_by_identity_id == actor_membership.identity_id
        ):
            return ApprovalDecisionPermission(True, "", "own")
        # Or if they own the session.
        owner_id = await _session_owner_identity_id(
            db, approval.session_id, approval.workspace_id
        )
        if owner_id is not None and owner_id == actor_membership.identity_id:
            return ApprovalDecisionPermission(True, "", "own")

    return ApprovalDecisionPermission(False, "approval.no_permission", "none")


async def evaluate_approval_visibility(
    db: AsyncSession,
    *,
    approval: Approval,
    actor_membership: Membership,
) -> bool:
    """Should ``actor_membership`` see this approval in lists?

    Decide rights imply view rights; auditors see all; otherwise view only
    your own session's approvals.
    """
    caps = capabilities_for(
        actor_membership.role
        if isinstance(actor_membership.role, str)
        else getattr(actor_membership.role, "value", None)
    )
    if "approvals.view_all" in caps:
        return True
    decision = await evaluate_approval_decision(
        db, approval=approval, actor_membership=actor_membership
    )
    return decision.allowed


# ──────────────────────────────────────────────────────────────────
# REST helpers
# ──────────────────────────────────────────────────────────────────
async def require_capability(membership: Membership | None, capability: str) -> None:
    if not has_capability(membership, capability):
        raise PermissionDenied(
            f"missing capability: {capability}",
            code=f"perm.{capability}",
        )


async def require_decide_approval(
    db: AsyncSession, *, approval: Approval, actor_membership: Membership
) -> str:
    decision = await evaluate_approval_decision(
        db, approval=approval, actor_membership=actor_membership
    )
    if not decision.allowed:
        raise PermissionDenied(
            "no permission to decide this approval",
            code="approval.no_permission",
        )
    return decision.matched_rule


# ──────────────────────────────────────────────────────────────────
# Internal lookups
# ──────────────────────────────────────────────────────────────────
async def _session_owner_identity_id(
    db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> uuid.UUID | None:
    sess = await db.get(Session, session_id)
    if sess is None or sess.workspace_id != workspace_id:
        return None
    return sess.owner_identity_id


async def _session_owner_department_id(
    db: AsyncSession, session_id: uuid.UUID, workspace_id: uuid.UUID
) -> uuid.UUID | None:
    """Department of the user that owns the session — falls back to None."""
    sess = await db.get(Session, session_id)
    if sess is None or sess.workspace_id != workspace_id or sess.owner_identity_id is None:
        return None
    from app.repositories.workspace import MembershipRepository  # local import: cycles

    mem = await MembershipRepository(db).get_by_identity_and_workspace(
        sess.owner_identity_id, workspace_id
    )
    if mem is None:
        return None
    return mem.department_id


__all__ = [
    "ROLE_CAPABILITIES",
    "ApprovalDecisionPermission",
    "capabilities_for",
    "evaluate_approval_decision",
    "evaluate_approval_visibility",
    "has_capability",
    "require_capability",
    "require_decide_approval",
]
