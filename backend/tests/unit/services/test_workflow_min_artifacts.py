"""Unit: ``min_artifacts_per_evolution`` gate (M2.3).

Walks the workflow's skip behaviour when there aren't enough failing
artifacts to justify burning aux-LLM budget. The bypass flag (used
by the manual trigger endpoint) overrides the gate but does not
bypass the workspace-disabled check or the breaker check.
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.platform_settings import EvolverSettings
from app.services import evolver_workflow as svc

pytestmark = pytest.mark.asyncio


def _patch_common(monkeypatch, *, summary: svc.DrainSummary, enabled: bool = True):
    async def _get_config(db, *, workspace_id):  # type: ignore[no-untyped-def]
        return EvolverSettings(enabled=enabled, engine="workflow")

    async def _breaker(*, bucket, workspace_id, trip_at):  # type: ignore[no-untyped-def]
        return False

    async def _drain(db, *, workspace_id, since, judge_score_max=0.0, limit=200):  # type: ignore[no-untyped-def]
        return summary

    audit_actions: list[str] = []

    async def _audit(**kwargs):  # type: ignore[no-untyped-def]
        audit_actions.append(kwargs.get("action") or "")

    monkeypatch.setattr(svc, "get_workspace_evolver_config", _get_config)
    monkeypatch.setattr(svc, "is_breaker_open", _breaker)
    monkeypatch.setattr(svc, "build_drain_summary", _drain)
    monkeypatch.setattr(svc, "_record_audit", _audit)
    return audit_actions


async def test_insufficient_artifacts_skips_with_reason(monkeypatch):
    """Four failing artifacts (< default min_artifacts=5) → skip."""
    summary = svc.DrainSummary(artifact_count=4)
    audit_actions = _patch_common(monkeypatch, summary=summary)

    result = await svc.evolve_workspace_skills_workflow(
        db=None,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        invocation_kind="scheduled",
    )
    assert result.skipped is True
    assert result.skip_reason == "insufficient_artifacts"
    assert result.proposals_created == 0
    assert result.artifacts_drained == 4
    assert svc.AUDIT_SKIPPED in audit_actions


async def test_bypass_flag_proceeds_past_min_artifacts(monkeypatch):
    """The bypass flag proceeds past the gate even with 1 artifact.

    To prove the gate is bypassed without spinning up the full LLM
    stack we also monkeypatch the aggregate stage to return zero
    clusters; the workflow should land at the publish stage with
    ``proposals_created=0`` and ``skipped=False``.
    """
    summary = svc.DrainSummary(
        artifact_count=1,
        score_distribution={-1: 1},
        common_error_kinds=[("hallucination", 1)],
        common_invoked_tools=[("web_search", 1)],
        sample_artifact_ids=[uuid.uuid4()],
        sample_run_ids=[uuid.uuid4()],
    )
    audit_actions = _patch_common(monkeypatch, summary=summary)

    async def _no_aux(db, *, workspace_id, task):  # type: ignore[no-untyped-def]
        return None

    async def _no_summary(db, *, workspace_id, summary, aux_config=None):  # type: ignore[no-untyped-def]
        return "stub"

    monkeypatch.setattr(svc, "get_aux_model", _no_aux)
    monkeypatch.setattr(svc, "summarize_drain_with_aux", _no_summary)
    monkeypatch.setattr(svc, "_aggregate_clusters", lambda summary, *, min_size=2: [])

    result = await svc.evolve_workspace_skills_workflow(
        db=None,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        invocation_kind="manual",
        bypass_min_artifacts=True,
    )
    assert result.skipped is False
    assert result.skip_reason is None
    assert result.proposals_created == 0
    assert result.artifacts_drained == 1
    assert svc.AUDIT_WORKFLOW_COMPLETED in audit_actions


async def test_bypass_does_not_override_disabled_workspace(monkeypatch):
    """Disabled workspaces still skip even with bypass=True."""
    summary = svc.DrainSummary(artifact_count=10)  # plenty of artifacts
    audit_actions = _patch_common(monkeypatch, summary=summary, enabled=False)

    result = await svc.evolve_workspace_skills_workflow(
        db=None,  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        invocation_kind="manual",
        bypass_min_artifacts=True,
    )
    assert result.skipped is True
    assert result.skip_reason == "evolver_disabled"
    assert svc.AUDIT_SKIPPED in audit_actions
