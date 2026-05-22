"""Platform-level KV settings reader/writer.

The store is a thin wrapper over :class:`~app.db.models.system_settings.SystemSetting`
plus a static defaults map. Callers prefer :func:`get_system_setting` for
reads (returns the merged value) and :func:`set_system_setting` for writes.

M0.13 will replace the manual defaults with a schema-driven catalog +
admin UI; until then we keep the surface minimal.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.system_settings import SystemSetting


class SystemSettingKey(StrEnum):
    REGISTRATION_MODE = "registration_mode"
    AUTH_REQUIRE_EMAIL_VERIFICATION = "auth_require_email_verification"
    RESERVED_WORKSPACE_SLUGS_EXTRA = "reserved_workspace_slugs_extra"
    EMAIL_VERIFICATION_TOKEN_TTL_SECONDS = "email_verification_token_ttl_seconds"
    RETENTION = "retention"
    WORKSPACE_QUOTA = "workspace_quota"
    MEMORY_DEFAULTS = "memory_defaults"
    NOTIFICATION_DEFAULTS = "notification_defaults"
    GENERAL = "general"
    AUTH_OAUTH = "auth_oauth"
    AUTH_MFA = "auth_mfa"
    EMAIL_SMTP = "email_smtp"
    WORKSPACE_DEFAULTS = "workspace_defaults"
    SECURITY_SHIELDS = "security_shields"
    SECURITY_SANDBOX = "security_sandbox"
    EVOLVER = "evolver"
    PLUGINS = "plugins"
    AUTH_REGISTER_RATE_LIMIT_PER_MINUTE = "auth_register_rate_limit_per_minute"
    INVITATION_EXPIRY_DAYS = "invitation_expiry_days"
    # Appended at the tail intentionally — Wave-3 sibling milestones may
    # be introducing their own keys; keeping new entries below the
    # historical block avoids merge conflicts on the diff.
    SKILL_INJECTION_DEFAULTS = "skill_injection_defaults"
    # M1.4 — platform-default thresholds for the nightly Skill Curator
    # sweep. Workspace overrides via ``home_config_json["curator"]`` win
    # when set; otherwise the Curator service reads these defaults.
    CURATOR_DEFAULTS = "curator_defaults"
    # M2.6 — full self-evolution config (replaces the M0.13 ``evolver``
    # placeholder). Workspace overrides ride
    # ``home_config_json["evolver"]``; the legacy ``evolver`` row is
    # auto-migrated by ``app.services.platform_settings._load_section``
    # on first read and supersedes itself on first write.
    EVOLVER_DEFAULTS = "evolver_defaults"
    # M2.5.3 — platform-level defaults for the provider failover chain.
    # Workspaces opt in by setting
    # ``home_config_json["providers"]["failover_enabled"] = True``;
    # ``chain_global_default`` here supplies the inherited chain when
    # the workspace leaves its own ``failover_chain`` empty.
    PROVIDER_FAILOVER = "provider_failover"
    # M2.5.6 — sub-agent batch spawn defaults. Workspace overrides via
    # ``home_config_json["subagent"]`` win when set; otherwise the
    # batch service reads these defaults to gate concurrency, batch
    # size, and nesting depth.
    SUBAGENT_BATCH_DEFAULTS = "subagent_batch_defaults"
    # M2.5.9 — provider-side cache marker defaults. Workspace overrides
    # ride ``home_config_json["providers"]["cache_control"]``; the
    # runner consults these defaults to decide whether to annotate the
    # outbound payload with cache breakpoints and how aggressively the
    # adaptive disable window should trip on consecutive misses.
    CACHE_CONTROL = "cache_control"
    # M3.1 — Skill Hub catalog defaults. Drives the federation surface.
    HUB = "hub"
    # M3.6 — cross-platform session routing defaults. Workspace
    # overrides ride ``home_config_json["session_routing"]``; the
    # dispatcher consults these defaults so a ``cross_platform_enabled``
    # flip propagates without a workspace edit. Default contract is
    # ``cross_platform_enabled=False`` and the legacy per-channel
    # routing path stays in force for existing workspaces.
    SESSION_ROUTING_DEFAULTS = "session_routing_defaults"
    # M4.3 — compaction / lineage replay defaults. Drives whether the
    # future sliding-window compaction layer keeps the M4.3 lineage
    # chain intact (``preserve_lineage`` default True) or falls back
    # to legacy "delete old turns". Workspace overrides via
    # ``home_config_json["compaction"]`` win when set. Read-only side
    # info — flipping the knob does not re-shape the LLM cache prefix.
    COMPACTION = "compaction"
    # M4.5 — cross-session insights defaults. Workspace overrides ride
    # ``home_config_json["insights"]``; the ARQ task + slash command
    # service consult these defaults to bound the lookback window and
    # cap the aux-LLM input size. ``enabled=False`` is the kill switch
    # that makes the slash command short-circuit before the breaker /
    # rate budget is consulted.
    INSIGHTS_DEFAULTS = "insights_defaults"


class RetentionSettings(BaseModel):
    """Platform retention policy (M0.11).

    ``physical_purge_enabled`` defaults to ``False`` so freshly upgraded
    deployments observe the dry-run reports for a week or two before
    flipping the switch. ``per_table_days`` lets compliance lengthen
    audit-adjacent tables (workspace_creation_logs) and shorten
    short-lived secrets (email_verification_tokens) without writing
    code. Schema lives here so M0.13 admin UI can validate updates
    against the same model.
    """

    default_days: int = Field(ge=1, le=3650, default=30)
    per_table_days: dict[str, int] = Field(
        default_factory=lambda: {
            "email_verification_tokens": 7,
            "workspace_creation_logs": 180,
        }
    )
    physical_purge_enabled: bool = False
    sweep_batch_size: int = Field(ge=10, le=10000, default=100)


class WorkspaceQuotaSettings(BaseModel):
    """Platform-wide workspace creation policy (M0.12).

    Defaults are deliberately conservative to stop a freshly opened
    deployment from being abused: self-registered + OAuth users get
    one slot (filled by the auto-provisioned personal workspace),
    creation by self-registered users is gated off entirely, and the
    ``creation_rate_per_period`` budget covers failed attempts as
    well so an attacker can't probe slug uniqueness for free.

    Tombstone retention controls how long the M0.12 ``slug_tombstoned``
    flag continues to block reuse after a workspace is deleted; today
    this is informational because the slug guard has no expiry, but
    the field is reserved for the M0.13 admin UI tunable.
    """

    default_per_self_registered: int = Field(ge=0, le=10000, default=1)
    default_per_oauth: int = Field(ge=0, le=10000, default=1)
    default_per_admin_created: int = Field(ge=0, le=10000, default=20)
    default_per_invitation_redeem: int = Field(ge=0, le=10000, default=0)
    count_only_owned_role: bool = True
    count_soft_deleted: bool = False
    creation_allowed_for_self_registered: bool = False
    creation_rate_per_period: int = Field(ge=1, le=1000, default=2)
    creation_rate_period_seconds: int = Field(ge=10, le=86400, default=3600)
    max_members_per_workspace_default: int = Field(ge=1, le=100000, default=50)
    grandfather_existing_users: bool = True
    tombstone_slug_lock_days: int = Field(ge=0, le=3650, default=90)


class MemoryDefaults(BaseModel):
    """Platform defaults for the cache-aware memory invariant (M0.7).

    ``always_on_max_chars`` is a conservative ceiling — sized so the
    system prompt budget after persona, tools, skills, and memory
    composition still fits comfortably inside a 32k-token context.
    Workspaces may raise it via ``home_config_json["memory"]
    ["always_on_max_chars"]`` when their model can absorb more.

    ``allow_immediate_default = False`` is the platform-wide default
    for the workspace ``allow_immediate`` gate. Workspace admins flip
    it on their tenant; agents that pass ``effective="now"`` against a
    workspace where it's still off receive a structured rejection
    rather than a hard error so the run continues.

    ``promotion_max_per_session`` bounds how many rows the post-FINAL
    promote hook drains in one pass — beyond it the rest defer to the
    workspace sweep cron so a misbehaving agent can't stall a chat
    turn with hundreds of pending writes.
    """

    always_on_max_chars: int = Field(ge=200, le=200_000, default=4000)
    allow_immediate_default: bool = False
    promotion_max_per_session: int = Field(ge=1, le=1000, default=50)
    max_failure_count_before_skip: int = Field(ge=1, le=20, default=3)


class NotificationDefaults(BaseModel):
    """Platform defaults for the notification fan-out (M0.10).

    ``spike_quota_ratio`` is the fraction of the rate window that
    triggers ``workspace.spike_detected`` — by default 80% of the
    creation budget. ``platform_email_critical_only`` is the kill
    switch that stops non-``requires_email`` events from ever sending
    mail; flip it off only after the SMTP transport ships in M0.13
    and the deployment can absorb the volume. ``in_app_retention_days``
    is the cleanup cron's purge horizon; raise it before lowering it
    because lowering deletes rows immediately on the next run.
    """

    spike_quota_ratio: float = Field(ge=0.1, le=1.0, default=0.8)
    default_cooldown_seconds: int = Field(ge=0, le=86400, default=600)
    platform_email_critical_only: bool = True
    in_app_retention_days: int = Field(ge=1, le=3650, default=90)
    email_transport_kind: str = Field(
        default="log",
        description=(
            "Identifier for :func:`get_email_transport` selection. "
            "M0.10 only honours 'log'; M0.13 will recognise 'smtp' / "
            "'mailgun' / 'ses'."
        ),
    )


class CuratorDefaults(BaseModel):
    """Platform defaults for the nightly Skill Curator sweep (M1.4).

    The Curator runs as a daily ARQ cron and operates per workspace.
    Each workspace can override any field via
    ``home_config_json["curator"]``; values here back-fill any
    unspecified knob and ship as the **opt-in** default for new
    workspaces (``enabled=True``).

    Pinned packs are exempt from automatic transitions regardless of
    these knobs — the exemption sits in
    :func:`app.services.skill_lifecycle.transition` (M1.1) and the
    Curator catches :class:`PackPinnedAutoSkipped` before continuing
    to the next pack.

    Knobs:

    * ``enabled`` — master switch. False on a workspace short-circuits
      the entire sweep for that tenant.
    * ``stale_after_days`` — how long an ACTIVE pack can go unused
      before the Curator proposes flipping it to STALE.
    * ``archive_after_days`` — how long a STALE pack waits before the
      Curator files an archive proposal in ``approvals``.
    * ``min_idle_hours`` — guards against racing a fresh use-event the
      rollup cron is about to write; an ACTIVE pack whose
      ``last_used_at`` is younger than this is left alone even when it
      is otherwise eligible.
    * ``active_skills_soft_cap`` — informational today; the M1.8
      runtime cap already drops surplus ACTIVE packs at injection
      time. Kept as configuration so the M1.9 admin UI can render a
      single "this is your soft cap" knob and so the Curator can use
      it later for more aggressive STALE marking once the workspace
      consistently exceeds the cap.
    """

    enabled: bool = True
    stale_after_days: int = Field(ge=1, le=3650, default=30)
    archive_after_days: int = Field(ge=1, le=3650, default=90)
    min_idle_hours: int = Field(ge=0, le=720, default=24)
    active_skills_soft_cap: int = Field(ge=1, le=10000, default=50)


class SkillInjectionDefaults(BaseModel):
    """Hard cap on simultaneously injected SkillPacks (M1.8).

    The runtime resolver in
    :func:`app.agents.harness.skills.build_skills_capability` consults
    these values (workspace overrides via
    ``home_config_json["skills"]`` win when set) before composing the
    SkillsCapability. Exceeding either cap drops unpinned packs by
    ascending priority, where priority is governed by
    ``selection_strategy``:

    * ``effectiveness_then_recency`` (default) — keeps packs with the
      highest ``effectiveness_avg``; ties break on most-recent
      ``last_used_at`` then earliest ``created_at``.
    * ``manual_only`` — preserves the caller's input order, intended
      for the M1.4 curator preview where the operator wants to see
      "what the runtime would inject from this exact list".

    Pinned packs are exempt from both caps — pin is the explicit
    "I accept the prompt cost" override; a warn log fires when the
    pinned set alone breaches the count cap so the breach is
    auditable.

    Char counting is a coarse proxy (``len(SKILL.md body)``) and
    intentionally ignores ``files_json`` attachments — files are
    addressable via tool calls and don't ride the always-on prompt.
    """

    max_active_injected: int = Field(ge=0, le=10000, default=30)
    max_injected_chars_total: int = Field(ge=0, le=10_000_000, default=12000)
    selection_strategy: str = Field(
        default="effectiveness_then_recency",
        pattern="^(effectiveness_then_recency|manual_only)$",
    )


def _platform_section_defaults() -> dict[str, Any]:
    """Lazy import keeps the schema layer's M0.13 dependency out of the
    cold-import path of every M0.x caller — ``app.schemas.platform_settings``
    pulls in pydantic models we don't want to load until first read.
    """
    from app.schemas.platform_settings import (
        AuthMfaSettings,
        AuthOAuthSettings,
        CacheControlDefaults,
        CompactionSettings,
        EmailSmtpSettings,
        EvolverSettings,
        GeneralSettings,
        HubSettings,
        InsightsSettings,
        PluginsSettings,
        ProviderFailoverDefaults,
        SecuritySandboxSettings,
        SecurityShieldsSettings,
        SessionRoutingDefaults,
        SubagentBatchDefaults,
        WorkspaceDefaultsSettings,
    )

    evolver_default = EvolverSettings().model_dump(mode="json")
    return {
        SystemSettingKey.GENERAL.value: GeneralSettings().model_dump(mode="json"),
        SystemSettingKey.AUTH_OAUTH.value: AuthOAuthSettings().model_dump(mode="json"),
        SystemSettingKey.AUTH_MFA.value: AuthMfaSettings().model_dump(mode="json"),
        SystemSettingKey.EMAIL_SMTP.value: EmailSmtpSettings().model_dump(mode="json"),
        SystemSettingKey.WORKSPACE_DEFAULTS.value: WorkspaceDefaultsSettings().model_dump(
            mode="json"
        ),
        SystemSettingKey.SECURITY_SHIELDS.value: SecurityShieldsSettings().model_dump(mode="json"),
        SystemSettingKey.SECURITY_SANDBOX.value: SecuritySandboxSettings().model_dump(mode="json"),
        # ``EVOLVER`` is the legacy placeholder row from M0.13. M2.6
        # promotes ``EVOLVER_DEFAULTS`` to canonical and only reads
        # ``EVOLVER`` for one-shot back-compat (see
        # ``platform_settings._load_section``). We seed both with the
        # same default so a fresh deployment has a consistent value
        # regardless of which key the caller looks at.
        SystemSettingKey.EVOLVER.value: evolver_default,
        SystemSettingKey.EVOLVER_DEFAULTS.value: evolver_default,
        SystemSettingKey.PLUGINS.value: PluginsSettings().model_dump(mode="json"),
        SystemSettingKey.PROVIDER_FAILOVER.value: (
            ProviderFailoverDefaults().model_dump(mode="json")
        ),
        SystemSettingKey.SUBAGENT_BATCH_DEFAULTS.value: (
            SubagentBatchDefaults().model_dump(mode="json")
        ),
        SystemSettingKey.CACHE_CONTROL.value: (CacheControlDefaults().model_dump(mode="json")),
        SystemSettingKey.HUB.value: HubSettings().model_dump(mode="json"),
        SystemSettingKey.SESSION_ROUTING_DEFAULTS.value: (
            SessionRoutingDefaults().model_dump(mode="json")
        ),
        SystemSettingKey.COMPACTION.value: (CompactionSettings().model_dump(mode="json")),
        SystemSettingKey.INSIGHTS_DEFAULTS.value: (InsightsSettings().model_dump(mode="json")),
    }


_DEFAULTS: dict[str, Any] = {
    SystemSettingKey.REGISTRATION_MODE.value: "open_personal",
    SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION.value: False,
    SystemSettingKey.RESERVED_WORKSPACE_SLUGS_EXTRA.value: [],
    SystemSettingKey.EMAIL_VERIFICATION_TOKEN_TTL_SECONDS.value: 86400,
    SystemSettingKey.RETENTION.value: RetentionSettings().model_dump(),
    SystemSettingKey.WORKSPACE_QUOTA.value: WorkspaceQuotaSettings().model_dump(),
    SystemSettingKey.MEMORY_DEFAULTS.value: MemoryDefaults().model_dump(),
    SystemSettingKey.NOTIFICATION_DEFAULTS.value: NotificationDefaults().model_dump(),
    SystemSettingKey.AUTH_REGISTER_RATE_LIMIT_PER_MINUTE.value: 3,
    SystemSettingKey.INVITATION_EXPIRY_DAYS.value: 30,
    SystemSettingKey.SKILL_INJECTION_DEFAULTS.value: SkillInjectionDefaults().model_dump(),
    SystemSettingKey.CURATOR_DEFAULTS.value: CuratorDefaults().model_dump(),
}


def _ensure_platform_defaults() -> None:
    """Resolve the lazy section defaults on first access; idempotent."""
    if SystemSettingKey.GENERAL.value in _DEFAULTS:
        return
    _DEFAULTS.update(_platform_section_defaults())


def _normalize_key(key: SystemSettingKey | str) -> str:
    return key.value if isinstance(key, SystemSettingKey) else str(key)


async def get_system_setting(
    db: AsyncSession,
    key: SystemSettingKey | str,
    default: Any = None,
) -> Any:
    """Return DB-stored value if present, else service default, else `default`."""
    key_str = _normalize_key(key)
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == key_str))
    ).scalar_one_or_none()
    if row is not None:
        return row.value_json
    _ensure_platform_defaults()
    if key_str in _DEFAULTS:
        return _DEFAULTS[key_str]
    return default


async def set_system_setting(
    db: AsyncSession,
    key: SystemSettingKey | str,
    value: Any,
) -> None:
    """Upsert a platform setting. Caller owns the transaction."""
    key_str = _normalize_key(key)
    stmt = (
        insert(SystemSetting)
        .values(key=key_str, value_json=value)
        .on_conflict_do_update(
            index_elements=[SystemSetting.key],
            set_={"value_json": value},
        )
    )
    await db.execute(stmt)


def get_default(key: SystemSettingKey | str) -> Any:
    """Service default for a known key (no DB hit). Returns ``None`` for unknown keys."""
    _ensure_platform_defaults()
    return _DEFAULTS.get(_normalize_key(key))


async def delete_system_setting(db: AsyncSession, key: SystemSettingKey | str) -> bool:
    """Drop the DB row for a key. Returns True iff a row existed.

    The next read falls through to ``_DEFAULTS`` (or any caller-supplied
    fallback). Used by the M0.13 admin "reset to default" action and
    the unit tests that need a clean slate.
    """
    from sqlalchemy import delete

    key_str = _normalize_key(key)
    res = await db.execute(delete(SystemSetting).where(SystemSetting.key == key_str))
    return bool(res.rowcount)
