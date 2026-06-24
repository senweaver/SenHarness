"""Agent routes: CRUD + /recent + /starred + /star."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel

from app.agents.harness.skills import BUNDLED_SKILLS_DIR
from app.agents.kernels.model_catalog import CatalogModel, list_models_for_provider
from app.agents.kernels.model_profile import resolve_profile

if TYPE_CHECKING:
    from app.db.models.model_provider import ProviderModel
from app.agents.kernels.model_client import ResolvedModel, resolve_for_agent
from app.agents.kernels.provider_catalog import get_entry as get_provider_entry
from app.agents.kernels.registry import describe as describe_runtimes
from app.api.deps import CurrentIdentityId, CurrentWorkspaceId, DBSession
from app.core.config import settings
from app.core.errors import Unauthorized
from app.repositories.agent import AgentRepository
from app.schemas.agent import (
    AgentCategory,
    AgentCloneIn,
    AgentCreate,
    AgentPublicCard,
    AgentRead,
    AgentRecent,
    AgentUpdate,
    StarAgentOut,
)
from app.schemas.audit import AgentReportIn, AgentReportRead
from app.services import agent as svc
from app.services import audit as audit_svc
from app.services import moderation as mod_svc
from app.services import workspace as ws_svc

router = APIRouter()


def _require_workspace(workspace_id: uuid.UUID | None) -> uuid.UUID:
    if workspace_id is None:
        raise Unauthorized("no_active_workspace", code="auth.no_active_workspace")
    return workspace_id


# ─── Runtime discovery ───────────────────────────────────
@router.get("/runtimes", summary="List registered Agent Runtimes")
async def list_runtimes() -> dict:
    """Enumerate every Agent Runtime registered in this deployment.

    Returns the runtime kind + display metadata + capabilities. Used by
    the Agent create/edit form to build the runtime picker, and by the
    workspace ``/settings/runtimes`` page to render the capability
    comparison cards.

    Public on purpose: the set of available runtimes is not sensitive
    (the kinds are visible in the frontend bundle anyway) and keeping
    this unauthenticated lets the login screen show a "Powered by: ..."
    footer without first exchanging a token.
    """
    runtimes = describe_runtimes()
    return {"runtimes": runtimes, "count": len(runtimes)}


# ─── List / create ───────────────────────────────────────
@router.get("", response_model=list[AgentRead])
async def list_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
) -> list[AgentRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agents = await AgentRepository(db).list_visible(
        workspace_id=ws_id, identity_id=identity_id, offset=offset, limit=limit
    )
    return [AgentRead.model_validate(a) for a in agents]


@router.post("", response_model=AgentRead, status_code=status.HTTP_201_CREATED)
async def create_agent(
    body: AgentCreate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.create_agent(
        db,
        workspace_id=ws_id,
        created_by=identity_id,
        **body.model_dump(),
    )
    await audit_svc.record(
        db,
        action="agent.create",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=agent.id,
        summary=f"created agent {agent.name!r}",
        metadata={"visibility": agent.visibility, "backend": agent.backend_kind},
        request=request,
    )
    await db.commit()
    return AgentRead.model_validate(agent)


# ─── Recent / starred (sidebar) ──────────────────────────
@router.get("/recent", response_model=list[AgentRecent])
async def list_recent_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    limit: int = Query(5, ge=1, le=50),
) -> list[AgentRecent]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await AgentRepository(db).recent_for_identity(
        workspace_id=ws_id, identity_id=identity_id, limit=limit
    )
    out: list[AgentRecent] = []
    for agent, last_at, msg_count, starred, pinned in rows:
        item = AgentRecent.model_validate(agent)
        item.last_message_at = last_at
        item.message_count = msg_count
        item.starred = starred
        item.pinned = pinned
        out.append(item)
    return out


# ─── Marketplace / discover ──────────────────────────────
@router.get("/discover", response_model=list[AgentPublicCard])
async def discover_public_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    q: str | None = Query(None, max_length=128),
    category: str | None = Query(None, max_length=64),
    tag: str | None = Query(None, max_length=64),
    template_only: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(60, ge=1, le=200),
) -> list[AgentPublicCard]:
    """Public agents across the platform, sorted by popularity (stars).

    The four optional filters compose with AND: ``category=engineering``
    + ``tag=react`` returns engineering agents tagged with React.
    ``template_only=true`` restricts to vendored built-in templates
    (handy for an "Official" tab in the marketplace).
    """
    # Caller must be in some workspace to access the marketplace.
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    rows = await AgentRepository(db).list_public_for_discovery(
        q=q,
        category=category,
        tag=tag,
        template_only=template_only,
        offset=offset,
        limit=limit,
    )
    out: list[AgentPublicCard] = []
    for agent, stars in rows:
        card = AgentPublicCard.model_validate(agent)
        card.stars = stars
        meta = agent.metadata_json or {}
        cat = meta.get("category")
        if isinstance(cat, str):
            card.category = cat
        raw_tags = meta.get("tags")
        if isinstance(raw_tags, list):
            card.tags = [str(t) for t in raw_tags if isinstance(t, str)]
        out.append(card)
    return out


@router.get("/discover/categories", response_model=list[AgentCategory])
async def discover_categories(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    template_only: bool = Query(False),
) -> list[AgentCategory]:
    """Sidebar source: 17 built-in categories + live public-agent counts.

    Always returns every category from
    :data:`app.agents.templates.catalog.CATEGORIES` (even at zero) so the
    UI can render the full list before templates are seeded. Ordering
    matches the catalog declaration.

    ``template_only`` restricts the counts to vendored templates so the
    sidebar matches a template-only listing (the create-agent dialog).
    """
    from app.agents.templates import catalog

    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    counts = await AgentRepository(db).count_by_category(template_only=template_only)
    return [
        AgentCategory(
            slug=c["slug"],
            name_cn=c["name_cn"],
            name_en=c["name_en"],
            count=counts.get(c["slug"], 0),
        )
        for c in catalog.CATEGORIES
    ]


@router.post(
    "/{agent_id}/clone",
    response_model=AgentRead,
    status_code=status.HTTP_201_CREATED,
)
async def clone_agent(
    agent_id: uuid.UUID,
    body: AgentCloneIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    """Clone a public agent into my active workspace."""
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    cloned = await svc.clone_public_agent(
        db,
        source_id=agent_id,
        target_workspace_id=ws_id,
        created_by=identity_id,
        name_override=body.name,
    )
    await audit_svc.record(
        db,
        action="marketplace.clone",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=cloned.id,
        summary=f"cloned agent {cloned.name!r} from marketplace",
        metadata={"source_id": str(agent_id)},
        request=request,
    )
    await db.commit()
    return AgentRead.model_validate(cloned)


@router.post(
    "/{agent_id}/report",
    response_model=AgentReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def report_agent(
    agent_id: uuid.UUID,
    body: AgentReportIn,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentReportRead:
    """Report a public agent for moderation review.

    No workspace gate — anyone with a valid session can flag a public agent.
    Audit row is tagged with the reporter's active workspace so their own
    workspace admin can see the action in the audit feed.
    """
    report = await mod_svc.submit_report(
        db,
        agent_id=agent_id,
        reporter_identity_id=identity_id,
        reason=body.reason,
        detail=body.detail,
    )
    await audit_svc.record(
        db,
        action="agent.report",
        actor_identity_id=identity_id,
        workspace_id=workspace_id,
        resource_type="agent",
        resource_id=agent_id,
        summary=f"reported agent {agent_id} ({body.reason})",
        metadata={"report_id": str(report.id), "reason": body.reason},
        request=request,
    )
    await db.commit()
    return AgentReportRead.model_validate(report)


@router.get("/starred", response_model=list[AgentRead])
async def list_starred_agents(
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[AgentRead]:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agents = await AgentRepository(db).starred_for_identity(
        workspace_id=ws_id, identity_id=identity_id
    )
    return [AgentRead.model_validate(a) for a in agents]


# ─── Single resource ─────────────────────────────────────
@router.get("/{agent_id}", response_model=AgentRead)
async def get_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    return AgentRead.model_validate(agent)


@router.patch("/{agent_id}", response_model=AgentRead)
async def update_agent(
    agent_id: uuid.UUID,
    body: AgentUpdate,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> AgentRead:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)

    patch = body.model_dump(exclude_none=True)
    old_visibility = agent.visibility
    updated = await AgentRepository(db).update(agent, **patch)

    # Visibility transitions (esp. → public) are high-signal audit events.
    if "visibility" in patch and patch["visibility"] != old_visibility:
        await audit_svc.record(
            db,
            action="agent.visibility_change",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="agent",
            resource_id=updated.id,
            summary=f"visibility {old_visibility} → {updated.visibility}",
            metadata={"from": old_visibility, "to": updated.visibility},
            request=request,
        )
    else:
        await audit_svc.record(
            db,
            action="agent.update",
            actor_identity_id=identity_id,
            workspace_id=ws_id,
            resource_type="agent",
            resource_id=updated.id,
            summary=f"updated agent {updated.name!r}",
            metadata={"fields": sorted(patch.keys())},
            request=request,
        )
    await db.commit()
    return AgentRead.model_validate(updated)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    request: Request,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_admin(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    await AgentRepository(db).soft_delete(agent)
    await audit_svc.record(
        db,
        action="agent.delete",
        actor_identity_id=identity_id,
        workspace_id=ws_id,
        resource_type="agent",
        resource_id=agent.id,
        summary=f"deleted agent {agent.name!r}",
        request=request,
    )
    await db.commit()


# ─── Star / pin ──────────────────────────────────────────
@router.post("/{agent_id}/star", response_model=StarAgentOut)
async def star_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
    pinned: bool = Query(False),
) -> StarAgentOut:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    starred, pinned_state = await svc.star_agent(
        db, identity_id=identity_id, agent_id=agent_id, pinned=pinned
    )
    await db.commit()
    return StarAgentOut(agent_id=agent_id, starred=starred, pinned=pinned_state)


@router.delete("/{agent_id}/star", status_code=status.HTTP_204_NO_CONTENT)
async def unstar_agent(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> None:
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)
    await svc.unstar_agent(db, identity_id=identity_id, agent_id=agent_id)
    await db.commit()


# ─── Agent-scoped skills (frontmatter only) ──────────────────
class AgentSkillCard(BaseModel):
    """Lightweight frontmatter projection for the chat composer's slash menu.

    The full SKILL.md body (license, examples, prompts) lives in the global
    ``GET /api/v1/skills`` endpoint; here we expose only what the slash
    palette needs to render a row + insert a token.
    """

    slug: str
    name: str
    description: str
    source: str  # "bundled" | "workspace"


@router.get("/{agent_id}/skills", response_model=list[AgentSkillCard])
async def list_agent_skills(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> list[AgentSkillCard]:
    """Skills enabled for ``agent_id`` — used by the chat composer's "/" palette.

    The agent's ``metadata_json.skills`` is the source of truth:
        - omitted / ``false`` → no skills.
        - ``true``            → every bundled + workspace skill.
        - ``list[str]``       → only the skills whose front-matter ``name``
                                appears in the list.

    The route returns a flat array of front-matter cards; the body /
    full content of each skill is fetched lazily via
    ``GET /api/v1/skills/{source}/{slug}`` when a user clicks "preview".
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)

    spec = (agent.metadata_json or {}).get("skills")
    if spec is None or spec is False:
        return []

    allowed: set[str] | None = None
    if isinstance(spec, list):
        allowed = {str(n).strip() for n in spec if n}
        if not allowed:
            return []

    out: list[AgentSkillCard] = []
    out.extend(_scan_skill_cards(BUNDLED_SKILLS_DIR, source="bundled"))
    out.extend(
        _scan_skill_cards(
            Path(settings.STORAGE_LOCAL_PATH) / "skills" / str(ws_id),
            source="workspace",
        )
    )
    if allowed is not None:
        out = [c for c in out if c.name in allowed or c.slug in allowed]
    out.sort(key=lambda c: (c.source == "workspace", c.name.lower()))
    return out


# ─── Model catalog (chat composer's ModelSelector) ──────────
class AgentModelOption(BaseModel):
    """One row in the chat composer's per-agent model dropdown.

    ``id`` is the ``provider:model`` selector — same shape
    ``RunRequest.model_override`` accepts and the frontend forwards
    verbatim on ``user_message`` frames.
    """

    id: str
    provider: str
    provider_display_name: str
    model: str
    name: str
    family: str
    recommended: bool = False
    description: str = ""
    is_default: bool = False
    # Catalog-resolved reasoning support so the composer can disable the
    # thinking mode for models that have no thinking phase. Mirrors the
    # runtime gating in ``model_profile.resolve_profile``.
    reasoning_supported: bool = False


class AgentModelsResponse(BaseModel):
    """Resolved provider context + the catalog spanning every enabled provider.

    ``provider`` is the agent-resolved default provider kind (used to
    highlight the active row). ``None`` only when the workspace has no
    enabled providers — the frontend hides the picker in that case.
    """

    provider: str | None
    default_model: str | None
    source: str | None  # "env" | "db" | "override" — origin of the resolved model
    options: list[AgentModelOption]


def _is_resolved_default(
    resolved: ResolvedModel | None,
    provider_kind: str,
    model_name: str,
) -> bool:
    return (
        resolved is not None
        and resolved.provider_kind == provider_kind
        and resolved.model_name == model_name
    )


def _reasoning_supported(
    provider_kind: str,
    model_name: str,
    db_metadata: dict | None = None,
) -> bool:
    return resolve_profile(
        provider_kind=provider_kind,
        model_name=model_name,
        db_metadata=db_metadata,
    ).reasoning.supported


def _composer_entries_for_provider(
    persisted: Sequence[ProviderModel],
    catalog: Sequence[CatalogModel],
) -> list[CatalogModel | ProviderModel]:
    """Rows offered in the chat composer for one enabled provider."""
    enabled_rows = [row for row in persisted if row.enabled]
    if enabled_rows:
        return list(enabled_rows)
    if persisted:
        return []
    return list(catalog)


@router.get("/{agent_id}/models", response_model=AgentModelsResponse)
async def list_agent_models(
    agent_id: uuid.UUID,
    db: DBSession,
    identity_id: CurrentIdentityId,
    workspace_id: CurrentWorkspaceId,
) -> AgentModelsResponse:
    """Return the models offered to the chat composer for this agent.

    The composer groups the dropdown by provider. For each workspace-
    enabled ``model_providers`` row we list only ``provider_models`` with
    ``enabled=true``. When a provider has no persisted model rows yet
    (operator never opened the Models tab), we fall back to the static
    catalog so a freshly-added provider is still usable.

    Only the (provider, model) pair the agent resolves to gets
    ``is_default=true``. When no providers are enabled the response
    carries ``provider=None`` and the UI hides the picker.
    """
    ws_id = _require_workspace(workspace_id)
    await ws_svc.ensure_member_access(db, workspace_id=ws_id, identity_id=identity_id)
    agent = await svc.get_agent_or_404(db, agent_id, workspace_id=ws_id)

    from sqlalchemy import select as _select

    from app.db.models.model_provider import ModelProvider, ProviderModel
    from app.repositories.provider import ProviderModelRepository

    stmt = (
        _select(ModelProvider)
        .where(
            ModelProvider.workspace_id == ws_id,
            ModelProvider.enabled.is_(True),
            ModelProvider.deleted_at.is_(None),
        )
        .order_by(
            ModelProvider.sort_order.asc(),
            ModelProvider.created_at.asc(),
        )
    )
    providers = list((await db.execute(stmt)).scalars().all())

    resolved = await resolve_for_agent(
        workspace_id=ws_id,
        agent_id=agent.id,
        override=None,
    )

    if not providers and resolved is None:
        return AgentModelsResponse(
            provider=None,
            default_model=None,
            source=None,
            options=[],
        )

    def _display_for(kind: str, fallback: str) -> str:
        # Custom OpenAI-compatible providers carry a user-given label on the
        # provider row; the catalog entry only holds the generic "Custom" name.
        if kind == "custom":
            return fallback
        entry = get_provider_entry(kind)
        if entry is None:
            return fallback
        return entry.display_name or fallback

    pm_repo = ProviderModelRepository(db)
    out: list[AgentModelOption] = []
    seen_ids: set[str] = set()
    for prov in providers:
        kind = str(prov.kind.value if hasattr(prov.kind, "value") else prov.kind)
        display = _display_for(kind, prov.name or kind)
        catalog_index = {e.model: e for e in list_models_for_provider(kind)}
        persisted = list(
            await pm_repo.list(
                provider_id=prov.id,
                order_by=(
                    ProviderModel.sort_order.asc(),
                    ProviderModel.created_at.asc(),
                ),
                limit=500,
            )
        )
        enabled_rows = [row for row in persisted if row.enabled]
        entries = _composer_entries_for_provider(persisted, list(catalog_index.values()))

        for entry in entries:
            if isinstance(entry, ProviderModel):
                model_id = entry.model
                meta = catalog_index.get(model_id)
                option = AgentModelOption(
                    id=f"{kind}:{model_id}",
                    provider=kind,
                    provider_display_name=display,
                    model=model_id,
                    name=entry.label or (meta.name if meta else model_id),
                    family=entry.family or (meta.family if meta else "custom"),
                    recommended=entry.recommended or (meta.recommended if meta else False),
                    description=meta.description if meta else "",
                    is_default=_is_resolved_default(resolved, kind, model_id),
                    reasoning_supported=_reasoning_supported(kind, model_id, entry.metadata_json),
                )
            else:
                option = AgentModelOption(
                    id=entry.id,
                    provider=kind,
                    provider_display_name=display,
                    model=entry.model,
                    name=entry.name,
                    family=entry.family,
                    recommended=entry.recommended,
                    description=entry.description,
                    is_default=_is_resolved_default(resolved, kind, entry.model),
                    reasoning_supported=_reasoning_supported(kind, entry.model),
                )
            if option.id in seen_ids:
                continue
            seen_ids.add(option.id)
            out.append(option)

        if prov.default_model:
            default_id = f"{kind}:{prov.default_model}"
            if default_id not in seen_ids and (
                not persisted or prov.default_model in {row.model for row in enabled_rows}
            ):
                seen_ids.add(default_id)
                out.append(
                    AgentModelOption(
                        id=default_id,
                        provider=kind,
                        provider_display_name=display,
                        model=prov.default_model,
                        name=prov.default_model,
                        family="custom",
                        recommended=False,
                        description="",
                        is_default=_is_resolved_default(resolved, kind, prov.default_model),
                        reasoning_supported=_reasoning_supported(kind, prov.default_model),
                    )
                )

    # If the resolved default isn't represented at all (env-only provider
    # not yet seeded in the DB) surface it explicitly so the trigger has
    # something to render.
    if resolved is not None:
        resolved_id = f"{resolved.provider_kind}:{resolved.model_name}"
        if resolved_id not in seen_ids:
            out.insert(
                0,
                AgentModelOption(
                    id=resolved_id,
                    provider=resolved.provider_kind,
                    provider_display_name=_display_for(
                        resolved.provider_kind, resolved.provider_kind
                    ),
                    model=resolved.model_name,
                    name=resolved.model_name,
                    family="custom",
                    recommended=False,
                    description="Workspace default — configured outside the static catalog.",
                    is_default=True,
                    reasoning_supported=_reasoning_supported(
                        resolved.provider_kind, resolved.model_name
                    ),
                ),
            )

    return AgentModelsResponse(
        provider=resolved.provider_kind if resolved else None,
        default_model=resolved.model_name if resolved else None,
        source=resolved.source if resolved else None,
        options=out,
    )


def _scan_skill_cards(root: Path, *, source: str) -> list[AgentSkillCard]:
    """Walk a skill directory and return one card per ``SKILL.md`` found.

    Mirrors ``app.api.v1.skills._scan`` but returns the lightweight
    ``AgentSkillCard`` projection (no body preview) — the chat composer
    only needs the slug/name/description tuple.
    """
    if not root.exists() or not root.is_dir():
        return []
    out: list[AgentSkillCard] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        md = sub / "SKILL.md"
        if not md.exists():
            continue
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm = _parse_frontmatter_minimal(text)
        slug = sub.name
        out.append(
            AgentSkillCard(
                slug=fm.get("name", slug) or slug,
                name=fm.get("name", slug) or slug,
                description=fm.get("description", ""),
                source=source,
            )
        )
    return out


def _parse_frontmatter_minimal(text: str) -> dict[str, str]:
    """Tiny YAML-ish parser. Only reads ``key: value`` pairs in the leading
    ``---``-fenced block; missing or malformed blocks return ``{}``.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    fm: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip("\"'")
    return fm
