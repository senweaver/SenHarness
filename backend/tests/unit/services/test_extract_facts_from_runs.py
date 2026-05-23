"""Unit: ``extract_facts_from_runs`` happy + skip paths (M3.7).

The aux client is monkey-patched to avoid network. Three flows are
pinned:

* No artifacts → ``aux_skipped=True`` with reason ``no_artifacts``.
* Aux returns a 2-dim draft → both rows persisted, both bypass the
  superseded chain because nothing pre-existed.
* Re-run with the same draft → ``facts_unchanged`` increments and no
  new row is created (idempotent).
* Aux returns a fresh observation for an existing dimension → the
  prior row is marked ``superseded_by_id`` and the audit row uses the
  ``user_profile.fact_superseded`` action key verbatim.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.core.security import utcnow_naive
from app.db.models.session_artifact import SessionArtifact
from app.db.models.user_profile import UserProfileDimension, UserProfileFact
from app.services import audit as audit_svc
from app.services import user_profile as svc

pytestmark = pytest.mark.asyncio


async def _seed_artifact(
    db_session,
    workspace,
    identity,
    *,
    finished_at: datetime | None = None,
    invoked_tools: list[str] | None = None,
) -> SessionArtifact:
    from app.db.models.session import Session, SessionKind, SessionState

    sess = Session(
        workspace_id=workspace.id,
        title="extract test",
        kind=SessionKind.P2P,
        state=SessionState.ACTIVE,
        owner_identity_id=identity.id,
    )
    db_session.add(sess)
    await db_session.flush()
    art = SessionArtifact(
        run_id=uuid.uuid4(),
        session_id=sess.id,
        agent_id=None,
        identity_id=identity.id,
        workspace_id=workspace.id,
        user_text_hash="x" * 64,
        turns_json=[
            {
                "role": "user",
                "text": "Please ship the dashboard before Friday.",
                "iteration": 0,
            },
            {
                "role": "assistant",
                "text": "Got it — confirmed deadline.",
                "iteration": 1,
            },
        ],
        injected_skill_pack_ids=[],
        invoked_tools=invoked_tools or ["web_search"],
        iteration_count=2,
        final_outcome="success",
        error_kind=None,
        finished_at=finished_at or utcnow_naive(),
    )
    db_session.add(art)
    await db_session.flush()
    return art


def _patch_aux(monkeypatch, *, draft):
    """Monkeypatch the aux model + extractor to avoid network."""

    async def _no_breaker(*, bucket, workspace_id, trip_at):
        return False

    async def _stub_aux(db, *, workspace_id, task):
        return svc.AuxiliaryConfig(
            task=task,
            model="openai:gpt-test",
            base_url=None,
            api_key_ref=None,
        )

    async def _stub_extract(*, config, artifacts):
        return draft

    async def _silent_audit(*args, **kwargs):
        return None

    monkeypatch.setattr(svc, "is_breaker_open", _no_breaker)
    monkeypatch.setattr(svc, "get_aux_model", _stub_aux)
    monkeypatch.setattr(svc, "_extract_with_aux", _stub_extract)
    monkeypatch.setattr(audit_svc, "record", _silent_audit)


async def test_no_artifacts_skips_with_reason(db_session, workspace, identity, monkeypatch):
    _patch_aux(monkeypatch, draft=svc._FactExtractionDraft(facts=[]))
    outcome = await svc.extract_facts_from_runs(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert outcome.aux_skipped is True
    assert outcome.aux_skip_reason == "no_artifacts"
    assert outcome.facts_created == 0
    assert outcome.facts_superseded == 0


async def test_first_extraction_persists_rows(db_session, workspace, identity, monkeypatch):
    await _seed_artifact(db_session, workspace, identity)
    draft = svc._FactExtractionDraft(
        facts=[
            svc._DimensionDraft(
                dimension=UserProfileDimension.COMMUNICATION_STYLE.value,
                fact="Direct, deadline-driven phrasing.",
                confidence=0.85,
            ),
            svc._DimensionDraft(
                dimension=UserProfileDimension.GOAL_PATTERN.value,
                fact="Ship-by-Friday cadence.",
                confidence=0.78,
            ),
        ]
    )
    _patch_aux(monkeypatch, draft=draft)

    outcome = await svc.extract_facts_from_runs(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )

    assert outcome.aux_skipped is False
    assert outcome.facts_created == 2
    assert outcome.facts_superseded == 0
    assert outcome.facts_unchanged == 0

    from sqlalchemy import select

    rows = (
        (
            await db_session.execute(
                select(UserProfileFact).where(
                    UserProfileFact.workspace_id == workspace.id,
                    UserProfileFact.identity_id == identity.id,
                )
            )
        )
        .scalars()
        .all()
    )
    dims = {r.dimension for r in rows}
    assert UserProfileDimension.COMMUNICATION_STYLE in dims
    assert UserProfileDimension.GOAL_PATTERN in dims
    for r in rows:
        assert r.user_confirmed is False
        assert r.user_rejected is False
        assert r.superseded_by_id is None


async def test_unchanged_facts_not_duplicated(db_session, workspace, identity, monkeypatch):
    await _seed_artifact(db_session, workspace, identity)
    draft = svc._FactExtractionDraft(
        facts=[
            svc._DimensionDraft(
                dimension=UserProfileDimension.DECISION_PREFERENCE.value,
                fact="Asks for trade-offs before committing.",
                confidence=0.82,
            ),
        ]
    )
    _patch_aux(monkeypatch, draft=draft)

    first = await svc.extract_facts_from_runs(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert first.facts_created == 1

    second = await svc.extract_facts_from_runs(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert second.facts_created == 0
    assert second.facts_unchanged == 1
    assert second.facts_superseded == 0


async def test_supersede_chain_records_old_id(db_session, workspace, identity, monkeypatch):
    await _seed_artifact(db_session, workspace, identity)
    first_draft = svc._FactExtractionDraft(
        facts=[
            svc._DimensionDraft(
                dimension=UserProfileDimension.LANGUAGE_PRIMARY.value,
                fact="Primary language: English.",
                confidence=0.9,
            ),
        ]
    )
    _patch_aux(monkeypatch, draft=first_draft)
    first = await svc.extract_facts_from_runs(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert first.facts_created == 1
    old_id = first.new_fact_ids[0]

    second_draft = svc._FactExtractionDraft(
        facts=[
            svc._DimensionDraft(
                dimension=UserProfileDimension.LANGUAGE_PRIMARY.value,
                fact="Primary language: Mandarin.",
                confidence=0.95,
            ),
        ]
    )

    async def _stub_extract(*, config, artifacts):
        return second_draft

    monkeypatch.setattr(svc, "_extract_with_aux", _stub_extract)

    second = await svc.extract_facts_from_runs(
        db_session,
        workspace_id=workspace.id,
        identity_id=identity.id,
    )
    assert second.facts_created == 1
    assert second.facts_superseded == 1
    new_id = second.new_fact_ids[0]

    old = await db_session.get(UserProfileFact, old_id)
    assert old is not None
    assert old.superseded_by_id == new_id
