"""Cross-session Insights platform / workspace settings (M4.5).

Drives the on-demand ``/insights [--days N]`` slash command. The same
shape backs both the platform-default row
(``system_settings.insights_defaults``, edited by platform admins via
``/admin/settings/insights``) and the per-workspace override at
``workspace.home_config_json["insights"]``; the workspace override wins
on a field-by-field basis (mirrors the M2.6 evolver pattern).

Defaults
--------

* ``enabled = True`` — the slash command is opt-out, not opt-in. The
  command itself is user-driven, so leaving it on by default keeps the
  feature discoverable while ``enabled=False`` is the kill switch
  workspace admins flip when they want to disable cross-session
  summarisation entirely (e.g. compliance review pending).
* ``default_days = 30`` and ``max_days = 90`` mirror the roadmap brief
  and bound the artifact scan window so a single command cannot fan
  out across the entire workspace history.
* ``max_artifacts_per_summary = 200`` caps the aux-LLM input size
  regardless of backlog volume — ``finished_at DESC LIMIT 200`` after
  the same-identity / same-workspace filter.
* ``aux_model`` defaults to ``None`` so the resolver falls through to
  ``aux_model_summarize`` → ``aux_model_judge`` → workspace default.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class InsightsSettings(BaseModel):
    """Workspace + platform-level cross-session insights config."""

    enabled: bool = True
    default_days: int = Field(ge=1, le=180, default=30)
    max_days: int = Field(ge=1, le=180, default=90)
    aux_model: str | None = Field(default=None, max_length=120)
    max_artifacts_per_summary: int = Field(ge=10, le=500, default=200)
    max_items_per_summary: int = Field(ge=1, le=20, default=7)

    @model_validator(mode="after")
    def _validate_days_window(self) -> InsightsSettings:
        if self.default_days > self.max_days:
            raise ValueError("default_days must not exceed max_days")
        return self
