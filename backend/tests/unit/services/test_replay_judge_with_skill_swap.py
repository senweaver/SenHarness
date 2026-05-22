"""Unit: ``skill_verifier.replay_judge_with_skill_swap`` (M2.4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agents.auxiliary_client import AuxiliaryConfig, AuxiliaryTask
from app.services import skill_verifier as verifier_svc

pytestmark = pytest.mark.asyncio


def _make_artifact_stub(*, turns: list[dict] | None = None, slug_text: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        turns_json=turns or [{"role": "user", "iteration": 0, "text": slug_text or "hi"}],
        invoked_tools=["search"],
        iteration_count=2,
        final_outcome="success",
        error_kind=None,
        finished_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=5),
    )


def _aux_config() -> AuxiliaryConfig:
    return AuxiliaryConfig(task=AuxiliaryTask.SKILL_REVIEW, model="test:fake")


async def _stub_aux_resolver(_db, *, workspace_id, task):
    _ = (workspace_id, task)
    return _aux_config()


async def test_replay_returns_old_new_pair_on_happy_path(db_session, workspace):
    artifact = _make_artifact_stub()

    call_payloads: list[str] = []

    async def fake_chat(*, config, system, user, response_format, timeout_s):
        _ = (config, system, timeout_s)
        call_payloads.append(user)
        # First call → old variant; second call → new variant.
        if "VARIANT=old" in user:
            return response_format(score=0, rationale="meh")
        return response_format(score=1, rationale="great")

    with (
        patch.object(verifier_svc, "get_aux_model", _stub_aux_resolver),
        patch.object(verifier_svc, "call_aux_chat", fake_chat),
    ):
        pair = await verifier_svc.replay_judge_with_skill_swap(
            db_session,
            workspace_id=workspace.id,
            artifact=artifact,
            pack_slug="my-skill",
            old_content="OLD body",
            new_content="NEW body",
        )

    assert pair.failed is False
    assert pair.old_score == 0
    assert pair.new_score == 1
    assert any("OLD body" in p for p in call_payloads)
    assert any("NEW body" in p for p in call_payloads)


async def test_replay_returns_zero_zero_when_first_call_raises(db_session, workspace):
    artifact = _make_artifact_stub()

    async def crash_chat(*, config, system, user, response_format, timeout_s):
        _ = (config, system, response_format, timeout_s)
        # Only the first variant blows up; the second still resolves.
        # The verifier's contract says ANY junk variant collapses the
        # whole pair to (0, 0, failed=True).
        if "VARIANT=old" in user:
            raise RuntimeError("aux exploded")
        return verifier_svc._ReplayScore(score=1, rationale="ok")

    with (
        patch.object(verifier_svc, "get_aux_model", _stub_aux_resolver),
        patch.object(verifier_svc, "call_aux_chat", crash_chat),
    ):
        pair = await verifier_svc.replay_judge_with_skill_swap(
            db_session,
            workspace_id=workspace.id,
            artifact=artifact,
            pack_slug="my-skill",
            old_content="OLD body",
            new_content="NEW body",
        )

    assert pair.failed is True
    assert pair.old_score == 0
    assert pair.new_score == 0


async def test_replay_returns_zero_zero_when_no_aux_configured(db_session, workspace):
    artifact = _make_artifact_stub()

    async def no_aux(_db, *, workspace_id, task):
        _ = (workspace_id, task)
        return None

    with patch.object(verifier_svc, "get_aux_model", no_aux):
        pair = await verifier_svc.replay_judge_with_skill_swap(
            db_session,
            workspace_id=workspace.id,
            artifact=artifact,
            pack_slug="my-skill",
            old_content=None,
            new_content="NEW body",
        )

    assert pair.failed is True
    assert pair.old_score == 0
    assert pair.new_score == 0


async def test_replay_truncates_long_turns_payload(db_session, workspace):
    big_text = "x" * 50_000
    artifact = _make_artifact_stub(
        turns=[
            {"role": "user", "iteration": 0, "text": big_text},
            {"role": "assistant", "iteration": 1, "text": "ok"},
        ]
    )
    captured: list[str] = []

    async def echo_chat(*, config, system, user, response_format, timeout_s):
        _ = (config, system, timeout_s)
        captured.append(user)
        return response_format(score=0, rationale="x")

    with (
        patch.object(verifier_svc, "get_aux_model", _stub_aux_resolver),
        patch.object(verifier_svc, "call_aux_chat", echo_chat),
    ):
        await verifier_svc.replay_judge_with_skill_swap(
            db_session,
            workspace_id=workspace.id,
            artifact=artifact,
            pack_slug="my-skill",
            old_content="OLD",
            new_content="NEW",
        )

    assert captured, "expected at least one captured prompt"
    for prompt in captured:
        # The turns payload alone is capped; the full prompt is allowed
        # to be a bit longer because of the framing header / SKILL body.
        assert "[truncated]" in prompt
        assert len(prompt) < verifier_svc._TURNS_REPLAY_BUDGET + 5_000


async def test_replay_falls_back_to_judge_aux_when_skill_review_missing(db_session, workspace):
    artifact = _make_artifact_stub()
    seen_tasks: list[AuxiliaryTask] = []

    async def fallback_resolver(_db, *, workspace_id, task):
        _ = workspace_id
        seen_tasks.append(task)
        if task == AuxiliaryTask.SKILL_REVIEW:
            return None
        return _aux_config()

    async def ok_chat(*, config, system, user, response_format, timeout_s):
        _ = (config, system, user, timeout_s)
        return response_format(score=1, rationale="ok")

    with (
        patch.object(verifier_svc, "get_aux_model", fallback_resolver),
        patch.object(verifier_svc, "call_aux_chat", ok_chat),
    ):
        pair = await verifier_svc.replay_judge_with_skill_swap(
            db_session,
            workspace_id=workspace.id,
            artifact=artifact,
            pack_slug="my-skill",
            old_content="OLD",
            new_content="NEW",
        )

    assert pair.failed is False
    assert AuxiliaryTask.SKILL_REVIEW in seen_tasks
    assert AuxiliaryTask.JUDGE in seen_tasks
