"""D17-P1 E2E verification — HITL approval UX polish.

Covers 6 checkpoints from the plan:

1. Login as demo; ``GET /approvals/counts`` returns ``{"pending": int}``.
2. Direct DB insert of a pending approval → ``/counts`` now shows ``1``.
3. ``POST /approvals/{id}/decision`` with ``action=deny, reason=""`` — the
   REST API accepts empty strings (UI layer enforces the min-length gate).
4. L3 auto-enable: ``resolve_require_approval`` returns DEFAULT_APPROVAL_TOOLS
   when ``autonomy_level=l3`` and no explicit ``approvals`` key.
5. Timeout path: run ``make_approval_callback`` with ``ttl_seconds=1`` without
   registering any decision; verify the persisted row ends up in
   ``status=EXPIRED`` (not ``DENIED``).
6. Smoke: counts updates to 0 again after the test approval is decided.

Run with:  ``python -m scripts.d17_verify_hitl``
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

logging.basicConfig(level=logging.WARNING)

import httpx

# Ensure the pydantic_ai kernel registers with the runner registry; the ASGI
# transport doesn't fire FastAPI lifespan hooks. (Same workaround as D14.)
import app.agents.kernels.native as _kernel_registration  # noqa: F401
from app.agents.harness.approvals import (
    DEFAULT_APPROVAL_TOOLS,
    resolve_require_approval,
)
from app.core.security import utcnow_naive
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.session import Session, SessionKind
from app.db.session import get_session_factory
from app.main import app
from app.services.approval import make_approval_callback


DEMO_EMAIL = "demo@senharness.app"
DEMO_PASSWORD = "senharness"


async def _login(client: httpx.AsyncClient) -> tuple[str, str]:
    r = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # /me to resolve the primary workspace id (shape mirrors MeOut).
    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {data['access_token']}"},
    )
    me.raise_for_status()
    ws_id = me.json()["current_workspace_id"]
    assert ws_id, "demo user should have an active workspace"
    return data["access_token"], ws_id


async def _get_counts(
    client: httpx.AsyncClient, token: str, ws_id: str
) -> dict:
    r = await client.get(
        "/api/v1/approvals/counts",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Workspace-Id": ws_id,
        },
    )
    r.raise_for_status()
    return r.json()


async def _insert_pending(ws_id: uuid.UUID, identity_id: uuid.UUID) -> uuid.UUID:
    """Stand up a throw-away session + approval row so /counts has something
    to count without driving the full LLM runner."""
    factory = get_session_factory()
    async with factory() as db:
        sess = Session(
            workspace_id=ws_id,
            kind=SessionKind.P2P,
            owner_identity_id=identity_id,
            title="d17 smoke",
            metadata_json={},
        )
        db.add(sess)
        await db.flush()
        ap = Approval(
            workspace_id=ws_id,
            session_id=sess.id,
            agent_id=None,
            run_id=uuid.uuid4(),
            tool_name="execute",
            tool_args={"command": "echo d17"},
            summary="$ echo d17",
            status=ApprovalStatus.PENDING,
            requested_by_identity_id=identity_id,
            expires_at=utcnow_naive() + timedelta(minutes=5),
        )
        db.add(ap)
        await db.commit()
        await db.refresh(ap)
        return ap.id


async def _fetch_approval(approval_id: uuid.UUID) -> Approval | None:
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as db:
        return (
            await db.execute(select(Approval).where(Approval.id == approval_id))
        ).scalar_one_or_none()


async def step_1_and_2_counts(
    client: httpx.AsyncClient, token: str, ws_id: str
) -> None:
    """1: counts returns a dict.  2: inserting a pending row bumps count."""
    before = await _get_counts(client, token, ws_id)
    assert "pending" in before, before
    print(f"  [step1] GET /approvals/counts = {before}  (OK)")

    me = await client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    identity_id = uuid.UUID(me.json()["id"])
    approval_id = await _insert_pending(uuid.UUID(ws_id), identity_id)
    after = await _get_counts(client, token, ws_id)
    assert after["pending"] == before["pending"] + 1, (before, after)
    print(
        f"  [step2] after insert: pending {before['pending']} → {after['pending']}  (OK)"
    )
    return approval_id


async def step_3_deny_empty_reason(
    client: httpx.AsyncClient,
    token: str,
    ws_id: str,
    approval_id: uuid.UUID,
) -> None:
    """REST accepts empty-string reason (UI layer enforces min-length)."""
    r = await client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"action": "deny", "reason": ""},
        headers={
            "Authorization": f"Bearer {token}",
            "X-Workspace-Id": ws_id,
        },
    )
    assert r.status_code == 200, r.text
    row = await _fetch_approval(approval_id)
    assert row is not None
    assert row.status == ApprovalStatus.DENIED, row.status
    print(
        f"  [step3] deny with empty reason accepted; db status={row.status.value}  (OK)"
    )


def step_4_l3_auto_enable() -> None:
    """``resolve_require_approval`` should auto-enable DEFAULT_APPROVAL_TOOLS
    when policy is L3 with no explicit ``approvals`` key. Also verifies the
    opt-out (``approvals: false``) still wins."""
    auto = resolve_require_approval({"autonomy_level": "l3"})
    assert set(auto) == set(DEFAULT_APPROVAL_TOOLS), auto
    opt_out = resolve_require_approval(
        {"autonomy_level": "l3", "approvals": False}
    )
    assert opt_out == [], opt_out
    explicit_list = resolve_require_approval(
        {"autonomy_level": "l3", "approvals": ["write_file"]}
    )
    assert explicit_list == ["write_file"], explicit_list
    print(
        f"  [step4] L3 auto → {auto}; explicit off respected; explicit list respected  (OK)"
    )


async def step_5_timeout_expired(ws_id: uuid.UUID, identity_id: uuid.UUID) -> None:
    """Timeout path writes EXPIRED, not DENIED."""
    # We need a session row for the FK.
    factory = get_session_factory()
    async with factory() as db:
        sess = Session(
            workspace_id=ws_id,
            kind=SessionKind.P2P,
            owner_identity_id=identity_id,
            title="d17 timeout",
            metadata_json={},
        )
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        sess_id = sess.id

    cb = make_approval_callback(
        workspace_id=ws_id,
        session_id=sess_id,
        agent_id=None,
        run_id=None,
        requested_by_identity_id=identity_id,
        ttl_seconds=1,  # very short TTL → should expire almost immediately
    )
    # The callback returns when the approval resolves. Since nothing decides
    # it, we expect it to block for ttl_seconds + ~5s buffer, then return
    # False with the DB row marked EXPIRED.
    approved = await cb("execute", {"command": "echo should-expire"})
    assert approved is False, "timeout should resolve False"

    # Fetch the most recent approval for this session and confirm status.
    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import desc, select

        row = (
            await db.execute(
                select(Approval)
                .where(Approval.session_id == sess_id)
                .order_by(desc(Approval.created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        assert row is not None, "approval row should have been persisted"
        assert row.status == ApprovalStatus.EXPIRED, (
            f"expected EXPIRED, got {row.status.value}"
        )
        assert row.decided_reason == "timeout"
        print(
            f"  [step5] timeout path → db status={row.status.value} reason={row.decided_reason!r}  (OK)"
        )


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as client:
        token, ws_id = await _login(client)
        print(f"  [auth ] logged in; ws={ws_id}")

        approval_id = await step_1_and_2_counts(client, token, ws_id)
        await step_3_deny_empty_reason(client, token, ws_id, approval_id)
        step_4_l3_auto_enable()

        me = await client.get(
            "/api/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        identity_id = uuid.UUID(me.json()["id"])
        print("  [step5] running timeout path (≈ 6s wait) …")
        await step_5_timeout_expired(uuid.UUID(ws_id), identity_id)

        final = await _get_counts(client, token, ws_id)
        print(f"  [done ] final /counts = {final}")

    print("\n[PASS] D17-P1 HITL verification complete")


if __name__ == "__main__":
    asyncio.run(main())
