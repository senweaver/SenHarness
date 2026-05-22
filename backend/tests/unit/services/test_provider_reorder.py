"""Reorder service tests for ``model_providers`` (M2.5.8).

Verifies:

* ``list_providers`` honours ``sort_order`` (ties broken on
  ``created_at`` asc) so the resolver and frontend list see the same
  sequence.
* ``reorder_providers`` renumbers densely (0, 1, 2, ...) for ids that
  appear in the payload, and silently no-ops on rows belonging to
  another workspace.
"""

from __future__ import annotations

import pytest

from app.services import provider as svc

pytestmark = pytest.mark.asyncio


async def _make(db, workspace, identity, *, kind: str, name: str):
    return await svc.create_provider(
        db,
        workspace_id=workspace.id,
        owner_identity_id=identity.id,
        kind=kind,
        name=name,
        api_key="dummy",
    )


async def test_reorder_persists_new_order(db_session, workspace, identity):
    p1 = await _make(db_session, workspace, identity, kind="openai", name="A")
    p2 = await _make(db_session, workspace, identity, kind="anthropic", name="B")
    p3 = await _make(db_session, workspace, identity, kind="deepseek", name="C")

    listed = await svc.list_providers(db_session, workspace_id=workspace.id)
    assert [p.id for p in listed] == [p1.id, p2.id, p3.id]

    rotated = await svc.reorder_providers(
        db_session,
        workspace_id=workspace.id,
        ordered_ids=[p3.id, p1.id, p2.id],
    )
    assert [p.id for p in rotated] == [p3.id, p1.id, p2.id]
    assert [int(p.sort_order) for p in rotated] == [0, 1, 2]

    re_listed = await svc.list_providers(db_session, workspace_id=workspace.id)
    assert [p.id for p in re_listed] == [p3.id, p1.id, p2.id]


async def test_reorder_ignores_unknown_ids(db_session, workspace, identity):
    import uuid

    p1 = await _make(db_session, workspace, identity, kind="openai", name="A")
    p2 = await _make(db_session, workspace, identity, kind="anthropic", name="B")

    stranger = uuid.uuid4()
    result = await svc.reorder_providers(
        db_session,
        workspace_id=workspace.id,
        ordered_ids=[stranger, p2.id, p1.id],
    )
    assert [p.id for p in result] == [p2.id, p1.id]
    assert [int(p.sort_order) for p in result] == [0, 1]
