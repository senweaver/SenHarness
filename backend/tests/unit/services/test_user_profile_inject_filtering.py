"""Unit: ``render_facts_for_injection`` filter rules (M3.7).

Pins the contract the runtime depends on:

* ``user_rejected=True`` → never injected.
* ``user_confirmed=False`` + ``confidence < 0.7`` → never injected
  (pending bucket — only the user can promote it).
* ``user_confirmed=False`` + ``confidence >= 0.7`` → auto-injected.
* ``user_confirmed=True`` → always injected (any confidence).
* The composed block respects the M0.7 always-on hard cap (4000 chars
  by default; the test uses a smaller cap to assert the trim).
* One bullet per dimension; superseded rows skipped.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.security import utcnow_naive
from app.db.models.user_profile import UserProfileDimension, UserProfileFact
from app.services import user_profile as svc

pytestmark = pytest.mark.asyncio


async def _seed(
    db_session,
    workspace,
    identity,
    *,
    dimension: UserProfileDimension,
    fact: str,
    confidence: float,
    user_confirmed: bool = False,
    user_rejected: bool = False,
    superseded_by_id: uuid.UUID | None = None,
    age_seconds: int = 0,
) -> UserProfileFact:
    row = UserProfileFact(
        workspace_id=workspace.id,
        identity_id=identity.id,
        dimension=dimension,
        fact=fact,
        confidence=confidence,
        source_run_ids=[],
        superseded_by_id=superseded_by_id,
        user_confirmed=user_confirmed,
        user_rejected=user_rejected,
    )
    db_session.add(row)
    await db_session.flush()
    if age_seconds:
        row.created_at = utcnow_naive() - timedelta(seconds=age_seconds)
        await db_session.flush()
    return row


async def test_low_confidence_unconfirmed_not_injected(
    db_session, workspace, identity
):
    await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.COMMUNICATION_STYLE,
        fact="Prefers concise responses.",
        confidence=0.6,
        user_confirmed=False,
    )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert rendered == ""


async def test_low_confidence_confirmed_is_injected(
    db_session, workspace, identity
):
    await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.COMMUNICATION_STYLE,
        fact="Prefers concise responses.",
        confidence=0.4,
        user_confirmed=True,
    )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert "## USER FACTS" in rendered
    assert "communication_style" in rendered
    assert "[✓]" in rendered


async def test_high_confidence_unconfirmed_is_injected(
    db_session, workspace, identity
):
    await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.DOMAIN_EXPERTISE,
        fact="Strong Python + SQL background.",
        confidence=0.85,
        user_confirmed=False,
    )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert "domain_expertise" in rendered


async def test_rejected_never_injected_even_at_high_confidence(
    db_session, workspace, identity
):
    await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.TONE_PREFERENCE,
        fact="Likes formal tone.",
        confidence=0.95,
        user_confirmed=False,
        user_rejected=True,
    )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert "tone_preference" not in rendered


async def test_confirmed_beats_unconfirmed_for_same_dimension(
    db_session, workspace, identity
):
    older_confirmed = await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.FORMALITY,
        fact="Casual register, first names.",
        confidence=0.5,
        user_confirmed=True,
        age_seconds=3600,
    )
    await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.FORMALITY,
        fact="Highly formal, prefers titles.",
        confidence=0.92,
        user_confirmed=False,
    )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert older_confirmed.fact.split(",")[0] in rendered
    assert "Highly formal" not in rendered


async def test_one_line_per_dimension(db_session, workspace, identity):
    for dim in UserProfileDimension:
        await _seed(
            db_session,
            workspace,
            identity,
            dimension=dim,
            fact=f"observation for {dim.value}",
            confidence=0.9,
        )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    bullet_lines = [
        line for line in rendered.splitlines() if line.startswith("- ")
    ]
    assert len(bullet_lines) == len(list(UserProfileDimension))


async def test_total_chars_capped_to_max(db_session, workspace, identity):
    # 12 dims * ~300 chars ≈ 3600 baseline. Force overflow by using a
    # tiny cap so the [truncated] sentinel must appear.
    for dim in UserProfileDimension:
        await _seed(
            db_session,
            workspace,
            identity,
            dimension=dim,
            fact="x" * 250,
            confidence=0.9,
        )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
        max_chars=600,
    )
    assert len(rendered) <= 600
    assert "[truncated]" in rendered


async def test_superseded_row_skipped(db_session, workspace, identity):
    new_row = await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.GOAL_PATTERN,
        fact="Goal: ship by Friday.",
        confidence=0.8,
    )
    await _seed(
        db_session,
        workspace,
        identity,
        dimension=UserProfileDimension.GOAL_PATTERN,
        fact="Old goal: prototype done.",
        confidence=0.95,
        superseded_by_id=new_row.id,
        age_seconds=86400,
    )
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert "Goal: ship by Friday" in rendered
    assert "Old goal" not in rendered


async def test_empty_state_returns_empty_string(
    db_session, workspace, identity
):
    rendered = await svc.render_facts_for_injection(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert rendered == ""
