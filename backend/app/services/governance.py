"""Governance service helpers."""

from __future__ import annotations

import uuid

from app.core.errors import Conflict, PermissionDenied
from app.db.models.governance import GovernanceScope


def ensure_scope_targets(
    *,
    scope: GovernanceScope | str,
    workspace_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    active_workspace_id: uuid.UUID | None,
    allow_global: bool,
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """Validate and normalize target ids by governance scope.

    Returns the normalized ``(workspace_id, agent_id)`` pair to store.
    """
    scope_value = GovernanceScope(str(scope))

    if scope_value == GovernanceScope.GLOBAL:
        if not allow_global:
            raise PermissionDenied("global_scope_forbidden", code="governance.global_forbidden")
        return None, None

    if active_workspace_id is None:
        raise Conflict("no_active_workspace", code="auth.no_active_workspace")

    if scope_value == GovernanceScope.WORKSPACE:
        if workspace_id and workspace_id != active_workspace_id:
            raise PermissionDenied(
                "workspace_scope_mismatch",
                code="governance.workspace_scope_mismatch",
            )
        return active_workspace_id, None

    # AGENT scope
    if agent_id is None:
        raise Conflict(
            "agent_scope_requires_agent_id",
            code="governance.agent_scope_requires_agent_id",
        )
    if workspace_id and workspace_id != active_workspace_id:
        raise PermissionDenied(
            "agent_scope_workspace_mismatch",
            code="governance.agent_scope_workspace_mismatch",
        )
    return active_workspace_id, agent_id
