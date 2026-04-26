"""Unit tests for governance scope normalization."""

from __future__ import annotations

import uuid

import pytest

from app.core.errors import Conflict, PermissionDenied
from app.db.models.governance import GovernanceScope
from app.services.governance import ensure_scope_targets


class TestEnsureScopeTargets:
    def test_global_requires_permission(self):
        with pytest.raises(PermissionDenied) as exc:
            ensure_scope_targets(
                scope=GovernanceScope.GLOBAL,
                workspace_id=None,
                agent_id=None,
                active_workspace_id=None,
                allow_global=False,
            )
        assert exc.value.code == "governance.global_forbidden"

    def test_global_normalizes_to_null_targets(self):
        ws_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        workspace_id, normalized_agent_id = ensure_scope_targets(
            scope=GovernanceScope.GLOBAL,
            workspace_id=ws_id,
            agent_id=agent_id,
            active_workspace_id=ws_id,
            allow_global=True,
        )
        assert workspace_id is None
        assert normalized_agent_id is None

    def test_workspace_scope_uses_active_workspace(self):
        ws_id = uuid.uuid4()
        workspace_id, agent_id = ensure_scope_targets(
            scope=GovernanceScope.WORKSPACE,
            workspace_id=None,
            agent_id=uuid.uuid4(),
            active_workspace_id=ws_id,
            allow_global=False,
        )
        assert workspace_id == ws_id
        assert agent_id is None

    def test_workspace_scope_rejects_other_workspace(self):
        with pytest.raises(PermissionDenied) as exc:
            ensure_scope_targets(
                scope=GovernanceScope.WORKSPACE,
                workspace_id=uuid.uuid4(),
                agent_id=None,
                active_workspace_id=uuid.uuid4(),
                allow_global=False,
            )
        assert exc.value.code == "governance.workspace_scope_mismatch"

    def test_agent_scope_requires_agent_id(self):
        with pytest.raises(Conflict) as exc:
            ensure_scope_targets(
                scope=GovernanceScope.AGENT,
                workspace_id=None,
                agent_id=None,
                active_workspace_id=uuid.uuid4(),
                allow_global=False,
            )
        assert exc.value.code == "governance.agent_scope_requires_agent_id"

    def test_agent_scope_normalizes_workspace(self):
        ws_id = uuid.uuid4()
        agent_id = uuid.uuid4()
        workspace_id, normalized_agent_id = ensure_scope_targets(
            scope=GovernanceScope.AGENT,
            workspace_id=None,
            agent_id=agent_id,
            active_workspace_id=ws_id,
            allow_global=False,
        )
        assert workspace_id == ws_id
        assert normalized_agent_id == agent_id
