"""Workspace department CRUD (admin-only)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.db.models.department import Department
from app.repositories.department import DepartmentRepository
from app.schemas.department import DepartmentCreate, DepartmentRead, DepartmentUpdate
from app.services import permissions as perm
from app.services import workspace as ws_svc

router = APIRouter(prefix="/departments", tags=["departments"])


def _read(d: Department) -> DepartmentRead:
    return DepartmentRead.model_validate(d)


@router.get("", response_model=list[DepartmentRead])
async def list_departments(
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
) -> list[DepartmentRead]:
    # Any member can list departments (used in member edit dropdowns).
    await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    from app.repositories.workspace import MembershipRepository

    rows = await DepartmentRepository(db).list_for_workspace(workspace_id)
    counts = await MembershipRepository(db).count_by_department(
        workspace_id=workspace_id
    )
    out: list[DepartmentRead] = []
    for d in rows:
        card = _read(d)
        card.member_count = counts.get(d.id, 0)
        out.append(card)
    return out


@router.post("", response_model=DepartmentRead, status_code=status.HTTP_201_CREATED)
async def create_department(
    payload: DepartmentCreate,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
) -> DepartmentRead:
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    await perm.require_capability(membership, "members.manage")

    repo = DepartmentRepository(db)
    parent = None
    path = payload.name
    if payload.parent_id is not None:
        parent = await repo.get(payload.parent_id)
        if parent is None or parent.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Parent department not found.",
            )
        path = f"{parent.path}/{payload.name}"

    row = await repo.create(
        workspace_id=workspace_id,
        parent_id=payload.parent_id,
        name=payload.name,
        path=path,
    )
    await db.commit()
    return _read(row)


@router.patch("/{department_id}", response_model=DepartmentRead)
async def update_department(
    department_id: uuid.UUID,
    payload: DepartmentUpdate,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
) -> DepartmentRead:
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    await perm.require_capability(membership, "members.manage")
    repo = DepartmentRepository(db)
    row = await repo.get(department_id)
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found."
        )

    # model_fields_set distinguishes "omitted" from "explicit null" — we need
    # that distinction to support move-to-root (``parent_id: null``).
    provided = payload.model_fields_set
    new_name = payload.name if "name" in provided else row.name
    if new_name != row.name and new_name is not None:
        row.name = new_name

    if "parent_id" in provided:
        if payload.parent_id is None:
            row.parent_id = None
        else:
            if payload.parent_id == row.id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot re-parent to self.",
                )
            parent = await repo.get(payload.parent_id)
            if parent is None or parent.workspace_id != workspace_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Parent department not found.",
                )
            # Refuse cycles: can't move a node under one of its descendants.
            if parent.path == row.path or parent.path.startswith(row.path + "/"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot move into own descendant.",
                )
            row.parent_id = payload.parent_id

    # Always recompute path so rename + move stay consistent.
    if row.parent_id is not None:
        parent = await repo.get(row.parent_id)
        row.path = f"{parent.path if parent else ''}/{row.name}".lstrip("/")
    else:
        row.path = row.name

    await db.commit()
    await db.refresh(row)
    return _read(row)


@router.delete("/{department_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_department(
    department_id: uuid.UUID,
    db: DBSession,
    workspace_id: CurrentWorkspaceId,
    identity_id: CurrentIdentityId,
) -> None:
    membership = await ws_svc.ensure_member_access(
        db, workspace_id=workspace_id, identity_id=identity_id
    )
    await perm.require_capability(membership, "members.manage")
    repo = DepartmentRepository(db)
    row = await repo.get(department_id)
    if row is None or row.workspace_id != workspace_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Department not found."
        )
    await repo.soft_delete(row)
    await db.commit()
