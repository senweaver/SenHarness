"""DTOs for the skill diff endpoints (M1.10)."""

from __future__ import annotations

from pydantic import Field

from app.schemas._base import ORMModel


class SkillDiffRequest(ORMModel):
    """Inputs for ``POST /skills/diff``.

    The 200 KB cap on each side matches ``SkillPackCreate.content_md``;
    pairs above that limit have to use the multipart-friendly
    ``render_multi_file_diff`` path internally.
    """

    old_content: str = Field(default="", max_length=200_000)
    new_content: str = Field(default="", max_length=200_000)
    context_lines: int = Field(default=3, ge=0, le=10)
    file_label: str = Field(default="SKILL.md", max_length=120)
    from_label: str = Field(default="old", max_length=40)
    to_label: str = Field(default="new", max_length=40)


class SkillDiffStats(ORMModel):
    added_lines: int
    removed_lines: int
    hunks: int


class SkillDiffResponse(ORMModel):
    diff: str
    stats: SkillDiffStats
    files_changed: list[str]
    truncated: bool
