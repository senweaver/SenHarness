"""Service-level tests for ``queue_insights_generation`` (M4.5).

Covers the three structural outcomes:

* happy path → audit ``insights.queued`` lands, ARQ enqueue is
  attempted (mocked), service returns the resolved day window.
* breaker open → audit ``insights.aux_skipped`` lands, the service
  raises ``InsightsBreakerOpen``.
* days out of range → ``ValidationFailed`` with stable code.

Aux LLM + ARQ pool are both mocked so the test suite stays free of
external dependencies.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.errors import ValidationFailed
from app.db.models.audit import AuditEvent
from app.services import cross_session_insights as insights_svc


pytestmark = pytest.mark.asyncio


async def _override_workspace_insights_settings(
    db_session, workspace, **fields
):
    """Stash a partial override on the workspace's home_config_json."""
    home = dict(workspace.home_config_json or {})
    insights_block = dict(home.get(insights_svc.INSIGHTS_WORKSPACE_KEY) or {})
    insights_block.update(fields)
    home[insights_svc.INSIGHTS_WORKSPACE_KEY] = insights_block
    workspace.home_config_json = home
    await db_session.flush()


async def test_queue_insights_happy_path(db_session, workspace, identity):
    return_session_id = uuid.uuid4()
    with patch.object(
        insights_svc,
        "_enqueue_generate",
        new=AsyncMock(return_value="job-1"),
    ):
        result = await insights_svc.queue_insights_generation(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            return_session_id=return_session_id,
            days=14,
        )
        await db_session.flush()
    assert result["queued"] is True
    assert result["days"] == 14
    assert result["expected_completion_seconds"] == 30
    assert result["job_id"] == "job-1"

    rows = (
        (
            await db_session.execute(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace.id,
                    AuditEvent.action == insights_svc.AUDIT_QUEUED,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    audit = rows[0]
    assert audit.metadata_json["days"] == 14
    assert audit.metadata_json["trigger"] == "slash_command"
    assert audit.resource_id == return_session_id


async def test_queue_insights_uses_workspace_default_when_days_unset(
    db_session, workspace, identity
):
    await _override_workspace_insights_settings(db_session, workspace, default_days=7)
    with patch.object(
        insights_svc,
        "_enqueue_generate",
        new=AsyncMock(return_value=None),
    ):
        result = await insights_svc.queue_insights_generation(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            return_session_id=uuid.uuid4(),
            days=None,
        )
    assert result["days"] == 7


async def test_queue_insights_rejects_days_above_max(
    db_session, workspace, identity
):
    with pytest.raises(ValidationFailed) as exc:
        await insights_svc.queue_insights_generation(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            return_session_id=uuid.uuid4(),
            days=999,
        )
    assert exc.value.code == "insights.days_out_of_range"


async def test_queue_insights_rejects_zero_days(
    db_session, workspace, identity
):
    with pytest.raises(ValidationFailed) as exc:
        await insights_svc.queue_insights_generation(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            return_session_id=uuid.uuid4(),
            days=0,
        )
    assert exc.value.code == "insights.days_out_of_range"


async def test_queue_insights_disabled_workspace_short_circuits(
    db_session, workspace, identity
):
    await _override_workspace_insights_settings(db_session, workspace, enabled=False)
    with pytest.raises(insights_svc.InsightsDisabled):
        await insights_svc.queue_insights_generation(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            return_session_id=uuid.uuid4(),
            days=10,
        )


async def test_queue_insights_breaker_open_audits_and_raises(
    db_session, workspace, identity
):
    """Force the shared evolver breaker to read open and assert the
    service raises ``InsightsBreakerOpen`` + writes the audit row.

    The audit row lands in a *fresh* DB session (so a caller-side
    rollback after the raise can't lose the breadcrumb), so we patch
    the dedicated helper instead of querying ``db_session`` directly.
    """

    async def fake_breaker(*, bucket, workspace_id, trip_at):
        _ = (bucket, workspace_id, trip_at)
        return True

    captured: dict = {}

    async def fake_audit(**kwargs):
        captured.update(kwargs)

    return_session_id = uuid.uuid4()
    with (
        patch.object(insights_svc, "is_breaker_open", fake_breaker),
        patch.object(
            insights_svc,
            "_audit_breaker_skipped_in_fresh_session",
            fake_audit,
        ),
        pytest.raises(insights_svc.InsightsBreakerOpen),
    ):
        await insights_svc.queue_insights_generation(
            db_session,
            workspace_id=workspace.id,
            identity_id=identity.id,
            return_session_id=return_session_id,
            days=14,
        )

    assert captured["workspace_id"] == workspace.id
    assert captured["resolved_days"] == 14
    assert captured["return_session_id"] == return_session_id
    # Silence unused import — the rows query is the alternate assertion
    # path when the helper is not patched (kept here for the
    # companion integration test in ``api/test_insights_endpoints.py``).
    _ = (select, AuditEvent)
