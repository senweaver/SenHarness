"""SkillPack runtime discovery — DB-backed binding from agent metadata.

The runtime resolves the bound pack ids from ``policy["skills"]`` (a list of
:class:`uuid.UUID` strings persisted on ``agents.metadata_json["skills"]`` by
:func:`app.api.v1.skills_persistence.bind_agent_skills`), filters them through
:meth:`app.repositories.skills.SkillPackRepository.list_active`, applies the
M1.8 hard-cap selection (count + char ceilings, pinned exempt), and returns
a SkillsCapability built from the resolved pack content together with the
list of pack ids that actually made it into the run.

The id list is consumed downstream by the M1.5 session_artifact + skill_usage
capture flow; surfacing it here keeps the resolution authoritative in one
place and avoids a second DB hit at capture time.

Eligibility — a row is injected when:

  * ``state == ACTIVE``, OR
  * ``pinned == true`` AND ``state != TOMBSTONE`` (manual pin overrides
    ``STALE`` / ``DEPRECATED`` etc., which auto-sweeps could otherwise
    have set; pinned rows are exempt from auto state moves so this
    cannot include rows that the curator silently archived).

Cap (M1.8): ``select_active_set`` then trims the eligible list to the
configured count + char ceilings — pinned packs win unconditionally,
unpinned packs sort by ``effectiveness_avg`` then ``last_used_at``.
Drops generate one ``skill_usage`` row each (event_kind=DROPPED_AT_CAP)
so the M1.4 curator can see which packs are repeatedly displaced and
move them to ``ARCHIVED``.

Failure mode is uniform: any branch that cannot produce a usable capability
returns ``(None, [])`` and logs at WARN level. The agent run never raises
through here. Cap audit and drop telemetry both fail-open — a failed write
never breaks the capability handoff.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.skills import SkillFile, SkillPack
from app.repositories.skills import SkillPackRepository
from app.services.skill_selection import (
    SkillSelectionResult,
    get_workspace_skill_config,
    select_active_set,
)

log = logging.getLogger(__name__)

# Filesystem location of the read-only bundled SkillPacks shipped with
# the image. The runtime no longer reads these for capability building —
# everything is DB-backed via :class:`SkillPackRepository` — but the
# admin API endpoints under ``app.api.v1.skills`` and ``app.api.v1.agents``
# still surface them as a listing source for the workspace UI's
# "available skills" picker.
BUNDLED_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"


async def build_skills_capability(
    *,
    policy: dict[str, Any] | None,
    workspace_id: uuid.UUID,
    db: AsyncSession,
    record_drops: bool = True,
    run_id: uuid.UUID | None = None,
    session_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    identity_id: uuid.UUID | None = None,
) -> tuple[Any | None, list[uuid.UUID]]:
    """Resolve the SkillPacks bound to this run, cap them, build a capability.

    Returns ``(capability, injected_pack_ids)``. ``capability`` is a
    ``SkillsCapability`` instance ready to attach to an Agent's
    ``capabilities=[...]`` list, or ``None`` when no packs survive
    resolution (the agent metadata has no bind, every bound pack is
    ineligible, the upstream library is missing, the materialisation
    raised, or the cap dropped every candidate).

    ``record_drops`` is the escape hatch tests use to skip the
    ``DROPPED_AT_CAP`` write — production callers always leave it on
    so the M1.4 curator has the signal it needs. ``run_id`` /
    ``session_id`` / ``agent_id`` / ``identity_id`` annotate the
    drop rows; when the resolver is invoked outside a real run
    (admin preview, smoke test) the runtime supplies ``None`` and the
    drop write synthesises placeholder anchors so the rows still
    land in the workspace's ``skill_usage`` history.

    The returned id list is the deterministic input to
    :func:`app.services.session_artifact.capture_from_run_outcome`'s
    ``injected_skill_pack_ids`` argument and to skill-usage telemetry.
    Order is the cap output order: pinned packs first (in the order
    the repository returned them), then surviving unpinned packs in
    cap-priority order. Two runs with the same DB state and the same
    cap config produce byte-identical system prompt prefixes.
    """

    bind_id_strs = _extract_bind_ids(policy)
    if not bind_id_strs:
        return (None, [])

    bind_ids: list[uuid.UUID] = []
    for raw in bind_id_strs:
        try:
            bind_ids.append(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            log.warning(
                "skills.malformed_pack_id workspace=%s value=%s",
                workspace_id,
                str(raw)[:60],
            )
    if not bind_ids:
        return (None, [])

    try:
        repo = SkillPackRepository(db)
        packs = list(
            await repo.list_active(workspace_id=workspace_id, ids=bind_ids)
        )
    except Exception:
        log.exception(
            "skills.discovery_failed workspace=%s pack_count=%d",
            workspace_id,
            len(bind_ids),
        )
        return (None, [])

    if not packs:
        return (None, [])

    # Load every SKILL.md body in one round-trip so the cap selector has
    # an accurate char count *and* the materialisation step doesn't pay
    # the per-pack file read again.
    body_by_pack = await _load_skill_md_bodies(db, packs=packs)
    for pack in packs:
        # Transient attribute — read by ``select_active_set`` via
        # ``getattr(pack, "content_md", ...)``. Not a SQLAlchemy
        # mapped column; never persisted.
        pack.content_md = body_by_pack.get(pack.id, "")  # type: ignore[attr-defined]

    cap_config = await get_workspace_skill_config(db, workspace_id=workspace_id)
    selection = select_active_set(packs, cap=cap_config)

    if selection.dropped and record_drops:
        await _record_dropped_at_cap(
            db,
            workspace_id=workspace_id,
            dropped=selection.dropped,
            run_id=run_id,
            session_id=session_id,
            agent_id=agent_id,
            identity_id=identity_id,
        )

    if selection.truncated_by_count or selection.truncated_by_chars:
        await _emit_cap_applied_audit(
            db,
            workspace_id=workspace_id,
            actor_identity_id=identity_id,
            agent_id=agent_id,
            selection=selection,
            cap_config=cap_config,
        )

    survivors = selection.selected
    if not survivors:
        return (None, [])

    injected_ids = [p.id for p in survivors]

    try:
        materialized = _materialize_skills_for_runtime(
            survivors, body_by_pack=body_by_pack
        )
    except Exception:
        log.exception(
            "skills.materialize_failed workspace=%s pack_count=%d",
            workspace_id,
            len(survivors),
        )
        return (None, [])

    if not materialized:
        return (None, [])

    capability = _instantiate_skills_capability(materialized)
    if capability is None:
        return (None, [])

    return (capability, injected_ids)


def _extract_bind_ids(policy: dict[str, Any] | None) -> list[str]:
    """Pull the bound pack id list out of the run policy.

    The shape comes from :func:`bind_agent_skills` which writes
    ``agent.metadata_json["skills"] = [str(uuid), ...]``; the runner
    forwards that value verbatim onto ``policy["skills"]``. Any other
    shape (bool / dict / scalar) is treated as "no bind" — the legacy
    ``true`` / ``false`` markers from the bundled-only era are no
    longer honoured here because they cannot map to a stable telemetry
    list of pack ids.
    """
    if not policy:
        return []
    spec = policy.get("skills")
    if not isinstance(spec, list):
        return []
    return [str(item) for item in spec if item]


async def _load_skill_md_bodies(
    db: AsyncSession, *, packs: Sequence[SkillPack]
) -> dict[uuid.UUID, str]:
    """Fetch every pack's ``SKILL.md`` body in one query.

    Falls back to ``pack.description`` when no row is present (matches
    the historical ``IMPORTED`` packs whose body lived only on a
    SkillPackVersion). Soft-deleted file rows are skipped — a live
    pack with a deleted SKILL.md surfaces with the description as the
    body.
    """
    if not packs:
        return {}
    pack_ids = [p.id for p in packs]
    try:
        rows = (
            await db.execute(
                sa_select(SkillFile).where(
                    SkillFile.skill_pack_id.in_(pack_ids),
                    SkillFile.path == "SKILL.md",
                    SkillFile.deleted_at.is_(None),
                )
            )
        ).scalars().all()
    except Exception:
        log.warning(
            "skills.skill_md_bulk_load_failed workspace_pack_count=%d",
            len(pack_ids),
            exc_info=True,
        )
        rows = []

    body_map: dict[uuid.UUID, str] = {}
    for row in rows:
        body_map[row.skill_pack_id] = row.content_md or ""
    for pack in packs:
        if pack.id in body_map:
            continue
        body_map[pack.id] = pack.description or ""
    return body_map


def _materialize_skills_for_runtime(
    packs: Sequence[SkillPack],
    *,
    body_by_pack: dict[uuid.UUID, str],
) -> list[Any]:
    """Build runtime ``Skill`` objects from each pack's pre-loaded body.

    Pure-Python — no DB calls — so the second pass after cap selection
    doesn't pay another query. Falls back to the pack description when
    the bulk load did not find a ``SKILL.md`` row.
    """
    try:
        skill_cls = _resolve_skill_dataclass()
    except Exception:
        log.warning("skills.dataclass_resolve_failed", exc_info=True)
        return []
    if skill_cls is None:
        return []

    out: list[Any] = []
    for pack in packs:
        body = body_by_pack.get(pack.id) or pack.description or ""
        try:
            out.append(
                skill_cls(
                    name=pack.slug,
                    description=(pack.description or pack.name or pack.slug)[:1024],
                    content=body,
                )
            )
        except TypeError:
            # The library evolved its dataclass field names between
            # versions. Rather than guess, log loudly and drop this
            # pack — better one missing skill than a hard crash on a
            # cosmetic field rename.
            log.warning(
                "skills.skill_dataclass_signature_mismatch pack=%s",
                pack.id,
                exc_info=True,
            )
    return out


async def _record_dropped_at_cap(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    dropped: list[SkillPack],
    run_id: uuid.UUID | None,
    session_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    identity_id: uuid.UUID | None,
) -> None:
    """Best-effort DROPPED_AT_CAP telemetry write.

    Synthesises ``run_id`` / ``session_id`` UUIDs when the caller
    didn't supply them (admin preview, smoke test). The telemetry
    rows are short-lived audit artifacts; a synthetic anchor still
    lets the curator aggregate "pack X dropped 27 times this week"
    without us having to invent a "no-run" sentinel. A write failure
    is logged and swallowed so the capability handoff still proceeds.
    """
    try:
        from app.db.models.skill_usage import SkillUsageEventKind
        from app.services.skill_usage import record_usage_batch

        await record_usage_batch(
            db,
            workspace_id=workspace_id,
            run_id=run_id or uuid.uuid4(),
            session_id=session_id or uuid.uuid4(),
            agent_id=agent_id,
            identity_id=identity_id,
            event_kind=SkillUsageEventKind.DROPPED_AT_CAP,
            pack_ids=[p.id for p in dropped],
        )
    except Exception:
        log.warning("skill.cap_drop_audit_failed", exc_info=True)


async def _emit_cap_applied_audit(
    db: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    actor_identity_id: uuid.UUID | None,
    agent_id: uuid.UUID | None,
    selection: SkillSelectionResult,
    cap_config,
) -> None:
    try:
        from app.services import audit as audit_svc

        await audit_svc.record(
            db,
            action="skill.cap_applied",
            actor_identity_id=actor_identity_id,
            workspace_id=workspace_id,
            resource_type="agent",
            resource_id=agent_id,
            summary=(
                f"injected {len(selection.selected)} packs / dropped "
                f"{len(selection.dropped)} (chars={selection.char_count})"
            ),
            metadata={
                "selected_count": len(selection.selected),
                "dropped_count": len(selection.dropped),
                "char_count": selection.char_count,
                "truncated_by_count": selection.truncated_by_count,
                "truncated_by_chars": selection.truncated_by_chars,
                "max_active_injected": cap_config.max_active_injected,
                "max_injected_chars_total": cap_config.max_injected_chars_total,
                "selection_strategy": cap_config.selection_strategy,
            },
        )
    except Exception:
        log.warning("skill.cap_audit_failed", exc_info=True)


def _resolve_skill_dataclass() -> Any | None:
    try:
        from pydantic_ai_skills import Skill  # type: ignore[import-not-found]

        return Skill
    except ImportError:
        pass
    try:
        from pydantic_ai_skills.types import (  # type: ignore[import-not-found]
            Skill,
        )

        return Skill
    except ImportError:
        log.info("pydantic-ai-skills not installed; skills disabled")
        return None


def _instantiate_skills_capability(materialized: list[Any]) -> Any | None:
    try:
        from pydantic_ai_skills import (  # type: ignore[import-not-found]
            SkillsCapability,
        )
    except ImportError:
        log.info("pydantic-ai-skills not installed; skills capability disabled")
        return None
    try:
        return SkillsCapability(
            skills=materialized, validate=False, auto_reload=False
        )
    except TypeError:
        try:
            return SkillsCapability(skills=materialized)
        except Exception:
            log.warning("SkillsCapability init failed", exc_info=True)
            return None
    except Exception:
        log.warning("SkillsCapability init failed", exc_info=True)
        return None
