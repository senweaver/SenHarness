"""Pure-ish unit tests for the M2.5 Approval TTL processor.

Verify per-``resource_type`` action selection: which expiry triggers
auto-execute vs reject. The DB-backed end-to-end sweep test lives in
``tests/integration/jobs/test_process_expired_approvals.py``.
"""

from __future__ import annotations

from app.db.models.approval import ApprovalResourceType
from app.jobs.approval_ttl import (
    AUDIT_EXPIRED_AUTO_EXECUTED,
    AUDIT_EXPIRED_REJECTED,
    AUDIT_EXPIRING_REMINDER_SENT,
    AUDIT_TTL_FAILED_PERMANENT,
)
from app.jobs.approval_ttl import _AUTO_EXECUTE_ON_EXPIRY


def test_auto_execute_set_only_contains_skill_pack_archive():
    """Roadmap TTL strategy table: only ``skill_pack_archive`` is an
    auto-archive on expiry; every other recognised verb must REJECT.
    """
    assert _AUTO_EXECUTE_ON_EXPIRY == frozenset(
        {ApprovalResourceType.SKILL_PACK_ARCHIVE.value}
    )


def test_audit_action_constants_match_brief():
    """Stable audit action keys must not drift — operators have
    already wired downstream alerting on these strings.
    """
    assert AUDIT_EXPIRED_AUTO_EXECUTED == "approval.expired_auto_executed"
    assert AUDIT_EXPIRED_REJECTED == "approval.expired_rejected"
    assert AUDIT_EXPIRING_REMINDER_SENT == "approval.expiring_reminder_sent"
    assert AUDIT_TTL_FAILED_PERMANENT == "approval.ttl_failed_permanent"


def test_skill_create_patch_edit_default_to_reject():
    """Per the roadmap table, skill_pack_create / patch / edit expire
    to REJECT (admin took longer than 14d to decide).
    """
    for rt in (
        ApprovalResourceType.SKILL_PACK_CREATE.value,
        ApprovalResourceType.SKILL_PACK_PATCH.value,
        ApprovalResourceType.SKILL_PACK_EDIT.value,
    ):
        assert rt not in _AUTO_EXECUTE_ON_EXPIRY


def test_delete_remove_file_write_file_default_to_reject():
    for rt in (
        ApprovalResourceType.SKILL_PACK_DELETE.value,
        ApprovalResourceType.SKILL_PACK_WRITE_FILE.value,
        ApprovalResourceType.SKILL_PACK_REMOVE_FILE.value,
    ):
        assert rt not in _AUTO_EXECUTE_ON_EXPIRY


def test_flow_create_defaults_to_reject():
    assert ApprovalResourceType.FLOW_CREATE.value not in _AUTO_EXECUTE_ON_EXPIRY
