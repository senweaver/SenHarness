"""Sub-agent batch spawn platform / workspace settings (M2.5.6).

Backs the ``subagent.batch`` admin section. Same shape powers both the
platform-wide default row (``system_settings.subagent_batch_defaults``)
and the per-workspace override at
``workspace.home_config_json["subagent"]``; the workspace block wins on
a field-by-field basis.

Defaults are deliberately conservative:

* ``batch_enabled = True`` — batches are valuable for parallel research
  / multi-source verification workflows; an opt-out workspace can flip
  the flag and degrade to serial single-child delegation transparently.
* ``max_concurrent = 5`` — protects the parent run from fan-out blowup
  while keeping the typical "research three sources in parallel"
  pattern uncongested.
* ``max_batch_size = 20`` — caps the amount of work a single
  ``delegate_batch`` tool call can request; setting this to ``1``
  forces serial fall-back without changing call sites.
* ``max_nesting_depth = 3`` — the ``spawn_depth`` column on
  :class:`~app.db.models.subagent_run.SubAgentRun` is enforced by the
  service layer; depth-3 was picked so a top-level user run can spawn
  two layers of helper batches before the gate trips.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SubagentBatchDefaults(BaseModel):
    """Workspace + platform-level batch-spawn config.

    The runtime resolver in
    :func:`app.services.subagent_batch_config.get_workspace_subagent_batch_config`
    deep-merges the per-workspace override onto these defaults; missing
    fields back-fill from the platform layer so an admin can flip a
    single knob (e.g. drop ``max_concurrent`` to 2) without re-stating
    the rest.
    """

    batch_enabled_default: bool = True
    max_batch_size_default: int = Field(ge=1, le=100, default=20)
    max_concurrent_default: int = Field(ge=1, le=20, default=5)
    max_nesting_depth_default: int = Field(ge=1, le=10, default=3)
