"""Tests for the ``WorkspaceType`` semantic label."""

from __future__ import annotations

from app.db.models.workspace import WorkspaceType


class TestWorkspaceType:
    def test_all_five_labels_present(self):
        # V1 ships exactly these five. Adding a new one is a minor
        # migration — but the existing five must stay stable so
        # operator workspaces created today still render correctly
        # tomorrow.
        assert WorkspaceType.COMPANY == "company"
        assert WorkspaceType.DEPARTMENT == "department"
        assert WorkspaceType.TEAM == "team"
        assert WorkspaceType.PROJECT == "project"
        assert WorkspaceType.TENANT == "tenant"

    def test_default_is_company(self):
        """Matches the DB ``server_default`` + the Python class default.

        If these drift apart the operator sees inconsistent behaviour
        between fresh installs and existing rows.
        """
        from app.db.models.workspace import Workspace

        # The model's default value must hit COMPANY.
        # We inspect the column default text; for StrEnum members this
        # stores the raw string.
        col = Workspace.__table__.c.workspace_type
        assert col.default is not None
        assert col.default.arg == "company"
        assert col.server_default is not None

    def test_membership_check(self):
        """Operators comparing a free-form string to the constants."""
        assert WorkspaceType.COMPANY == "company"
        assert WorkspaceType.TENANT == "tenant"
        assert WorkspaceType.COMPANY != "unknown-future"
