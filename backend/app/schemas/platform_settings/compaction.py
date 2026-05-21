"""Compaction / lineage replay platform settings (M4.3).

Backs the ``compaction`` admin section. Drives whether the future
sliding-window compaction layer keeps the M4.3 lineage chain intact
(``preserve_lineage=True``, default) or falls back to the legacy
"delete old turns" path. The runtime never feeds the resulting
``original_turns_ref`` metadata into the LLM context, so flipping
this knob does not re-shape the cache prefix on subsequent turns —
it only changes whether the audit / debug surface can trace a
compressed summary back to the originating turns.

Knobs:

* ``preserve_lineage`` — master switch. When True, compactions stamp
  the summary message with ``original_turns_ref`` and the originals
  with ``compressed_into_summary_id`` so the M4.3 replay endpoint
  can resolve them. False degrades to the M3.x baseline behaviour
  (delete old turns) for operators who explicitly do not want the
  history to outlive its compaction.
* ``max_keep_turns`` — soft target for the sliding-window head: the
  most recent N turns stay verbatim; older turns are folded into a
  single summary message. The actual sliding-window implementation
  ships in a follow-up; this knob is wired so the future caller can
  read it without a config schema bump.
* ``aux_summarize_max_tokens`` — output budget for the auxiliary
  summarisation call. Ten times the typical chat reply, intended to
  let one summary message absorb a long-tail of historical turns
  without truncating prematurely.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CompactionSettings(BaseModel):
    preserve_lineage: bool = True
    max_keep_turns: int = Field(ge=10, le=500, default=50)
    aux_summarize_max_tokens: int = Field(ge=200, le=2000, default=500)
