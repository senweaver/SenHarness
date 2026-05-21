"""Sidebar aggregator DTOs.

Wire schema for ``GET /api/v1/sidebar/my-items`` which unions
starred Agents, Squads and Sessions for the current identity in
the active workspace.

``type`` is a plain string literal — adding a future ``"goal"``
entry must require no migration and no schema break.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas._base import ORMModel

SidebarItemType = Literal["agent", "squad", "session"]


class SidebarItem(ORMModel):
    type: SidebarItemType
    id: uuid.UUID
    name: str
    avatar_seed: str
    pinned: bool = False
    unread_count: int = 0
    last_activity_at: datetime | None = None
    href: str


class SidebarItemsResponse(ORMModel):
    items: list[SidebarItem] = Field(default_factory=list)
    total: int = 0
