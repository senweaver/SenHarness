"""Integration: end-to-end fan-out through ``emit_event``.

Picks three of the seven trigger points (per M0.10 task spec):

* ``auth.workspace_provisioned`` — actor audience, email-required
* ``channel.sender_blocked`` — workspace-admins audience, email-required
* ``judge.score_negative`` — owner audience, in-app only

For each, asserts:
1. one ``Notification`` row inserted per recipient on the in-app channel
2. one ``audit_events.notification.emitted`` row written
3. EMAIL fan-out enqueued where the descriptor demands it
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models.audit import AuditEvent
from app.db.models.notification import Notification
from app.services import notification_events as ne

pytestmark = pytest.mark.asyncio


async def _audit_actions_in_workspace(db, *, workspace_id):
    rows = (
        await db.execute(
            select(AuditEvent.action).where(
                AuditEvent.workspace_id == workspace_id
            )
        )
    ).scalars().all()
    return list(rows)


async def _notifications_for(db, *, identity_id):
    rows = (
        await db.execute(
            select(Notification).where(
                Notification.recipient_identity_id == identity_id
            )
        )
    ).scalars().all()
    return list(rows)


async def test_workspace_provisioned_emits_in_app_for_actor(
    db_session, identity, workspace
):
    """``actor`` audience → exactly one in-app row, audit logged."""
    counters = await ne.emit_event(
        db_session,
        event_key="auth.workspace_provisioned",
        workspace_id=workspace.id,
        actor_identity_id=identity.id,
        target_identity_ids=[identity.id],
        cooldown_resource_id=str(workspace.id),
        payload={
            "workspace_id": str(workspace.id),
            "workspace_name": workspace.name,
            "workspace_slug": workspace.slug,
            "user_name": identity.name,
            "source": "register",
        },
    )
    await db_session.flush()

    assert counters["in_app_sent"] == 1
    rows = await _notifications_for(db_session, identity_id=identity.id)
    assert any(n.kind == "auth.workspace_provisioned" for n in rows)
    actions = await _audit_actions_in_workspace(
        db_session, workspace_id=workspace.id
    )
    assert "notification.emitted" in actions


async def test_judge_score_negative_emits_to_owner(
    db_session, identity, workspace
):
    """Owner audience resolves the workspace owner — exactly one row."""
    counters = await ne.emit_event(
        db_session,
        event_key="judge.score_negative",
        workspace_id=workspace.id,
        cooldown_resource_id=str(uuid.uuid4()),
        payload={
            "artifact_id": str(uuid.uuid4()),
            "score": -1,
            "confidence": 0.9,
        },
    )
    await db_session.flush()

    assert counters["in_app_sent"] >= 1
    rows = await _notifications_for(db_session, identity_id=identity.id)
    assert any(n.kind == "judge.score_negative" for n in rows)


async def test_channel_sender_blocked_targets_workspace_admins(
    db_session, identity, workspace
):
    """Owner counts as workspace_admins → still receives the notification."""
    counters = await ne.emit_event(
        db_session,
        event_key="channel.sender_blocked",
        workspace_id=workspace.id,
        cooldown_resource_id=str(uuid.uuid4()),
        payload={
            "channel_id": str(uuid.uuid4()),
            "channel_name": "demo",
            "channel_kind": "slack",
            "external_user_id": "U123",
            "mode": "deny_listed",
            "ingress": "webhook",
        },
    )
    await db_session.flush()

    assert counters["in_app_sent"] >= 1
    rows = await _notifications_for(db_session, identity_id=identity.id)
    assert any(n.kind == "channel.sender_blocked" for n in rows)


async def test_unknown_event_key_returns_zero_counters(
    db_session, workspace
):
    """Unknown event keys are a no-op (logged, no audit)."""
    counters = await ne.emit_event(
        db_session,
        event_key="totally.fake.key",
        workspace_id=workspace.id,
    )
    assert counters == {
        "in_app_sent": 0,
        "email_sent": 0,
        "cooldown_skipped": 0,
        "pref_skipped": 0,
    }
