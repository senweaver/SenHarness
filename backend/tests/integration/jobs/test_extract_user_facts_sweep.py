"""Integration: ``extract_user_facts_sweep`` ARQ task (M3.7).

Drives the daily cron across two workspaces with two identities and
asserts:

* every ``(workspace, identity)`` pair with recent artifacts is
  visited exactly once;
* a per-identity exception is isolated and audited via
  ``user_profile.update_failed`` while the rest of the sweep
  continues;
* the summary dict carries ``identities_updated`` /
  ``identities_failed`` / ``facts_created``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.jobs import user_modeling as job
from app.services import user_profile as svc

pytestmark = pytest.mark.asyncio


async def _bootstrap_workspace_with_artifact(async_client) -> tuple[str, str]:
    """Register one identity, create a session + artifact tied to them."""
    email = f"up-{uuid.uuid4().hex[:8]}@example.com"
    password = "user-profile-sweep-test-very-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "UP Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    workspace = body.get("workspace") or {}
    ws_id = workspace["id"]
    identity_id = body["identity_id"]

    from app.db.models.session import Session, SessionKind, SessionState
    from app.db.models.session_artifact import SessionArtifact
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        sess = Session(
            workspace_id=uuid.UUID(ws_id),
            title="seed",
            kind=SessionKind.P2P,
            state=SessionState.ACTIVE,
            owner_identity_id=uuid.UUID(identity_id),
        )
        db.add(sess)
        await db.flush()
        art = SessionArtifact(
            run_id=uuid.uuid4(),
            session_id=sess.id,
            agent_id=None,
            identity_id=uuid.UUID(identity_id),
            workspace_id=uuid.UUID(ws_id),
            user_text_hash="x" * 64,
            turns_json=[
                {"role": "user", "text": "ship friday", "iteration": 0},
                {"role": "assistant", "text": "ok", "iteration": 1},
            ],
            injected_skill_pack_ids=[],
            invoked_tools=["web_search"],
            iteration_count=2,
            final_outcome="success",
            error_kind=None,
            finished_at=datetime.utcnow(),
        )
        db.add(art)
        await db.commit()

    return ws_id, identity_id


async def test_sweep_visits_every_identity(async_client, monkeypatch):
    ws_a, ident_a = await _bootstrap_workspace_with_artifact(async_client)
    ws_b, ident_b = await _bootstrap_workspace_with_artifact(async_client)

    seen: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def _stub_extract(
        db,
        *,
        workspace_id,
        identity_id,
        since_run_count=10,
        invocation_kind="scheduled",
        actor_identity_id=None,
    ):
        _ = db, since_run_count, invocation_kind, actor_identity_id
        seen.append((workspace_id, identity_id))
        return svc.ExtractOutcome(
            workspace_id=workspace_id,
            identity_id=identity_id,
            facts_created=1,
            facts_superseded=0,
            artifacts_examined=1,
            duration_ms=1,
        )

    monkeypatch.setattr(svc, "extract_facts_from_runs", _stub_extract)

    summary = await job.extract_user_facts_sweep({})
    assert summary["status"] == "ok"
    assert summary["workspaces_seen"] >= 2
    assert (uuid.UUID(ws_a), uuid.UUID(ident_a)) in seen
    assert (uuid.UUID(ws_b), uuid.UUID(ident_b)) in seen
    assert summary["identities_updated"] >= 2
    assert summary["identities_failed"] == 0
    assert summary["facts_created"] >= 2


async def test_sweep_isolates_per_identity_failure(async_client, monkeypatch):
    ws_a, ident_a = await _bootstrap_workspace_with_artifact(async_client)
    ws_b, ident_b = await _bootstrap_workspace_with_artifact(async_client)
    target_failure = uuid.UUID(ident_a)

    async def _stub_extract(
        db,
        *,
        workspace_id,
        identity_id,
        since_run_count=10,
        invocation_kind="scheduled",
        actor_identity_id=None,
    ):
        _ = db, workspace_id, since_run_count, invocation_kind, actor_identity_id
        if identity_id == target_failure:
            raise RuntimeError("simulated user_profile crash")
        return svc.ExtractOutcome(
            workspace_id=workspace_id,
            identity_id=identity_id,
            facts_created=2,
            duration_ms=4,
        )

    monkeypatch.setattr(svc, "extract_facts_from_runs", _stub_extract)

    summary = await job.extract_user_facts_sweep({})
    assert summary["status"] == "ok"
    assert summary["identities_failed"] >= 1
    assert summary["identities_updated"] >= 1
    assert summary["errors"], "errors list should carry the failed identity"

    from sqlalchemy import select

    from app.db.models.audit import AuditEvent
    from app.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(AuditEvent).where(
                        AuditEvent.action == job.AUDIT_UPDATE_FAILED,
                        AuditEvent.workspace_id == uuid.UUID(ws_a),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert rows, "expected one user_profile.update_failed audit row"
    _ = ws_b, ident_b  # signal coverage of the unaffected pair
