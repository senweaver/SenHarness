"""Unified platform-admin settings registry, cache, and CRUD (M0.13).

Section model
-------------

Every page rendered under ``/admin/settings/<section>`` resolves to one
:class:`PlatformSettingsSection` value. Each section carries:

* a Pydantic schema (canonical model defined in the owning milestone —
  M0.7 / M0.10 / M0.11 / M0.12 contributed four; M0.9 contributed
  ``AuthRegistrationSettings``; the remaining nine ship in M0.13) that
  validates updates;
* a :class:`SystemSettingKey` (where the persisted JSON row lives in
  ``system_settings``);
* an optional ``ENV_FIELD_MAPPING`` entry telling the bootstrap step
  which ``Settings`` attribute to seed each field from on first start;
* an optional set of dangerous fields whose change requires explicit
  ``confirmed_dangerous=True`` and writes a separate audit row.

Five sections (``auth.registration``, ``email.smtp``, ``auth.oauth``,
``security.sandbox``, ``security.shields``) span more than one
``SystemSettingKey`` row — for example ``auth.registration`` writes
both the legacy ``REGISTRATION_MODE`` and
``AUTH_REQUIRE_EMAIL_VERIFICATION`` keys so existing readers keep
working without code changes. Splitting / merging is transparent to
the caller; ``get_section`` / ``update_section`` always speak the
section schema.

Cache
-----

Reads are served from an in-process LRU bucket with a short TTL
(:data:`_CACHE_TTL_SECONDS`). Writes invalidate the local entry
synchronously and broadcast the section name on the Redis channel
:data:`PLATFORM_SETTINGS_CHANNEL`; every backend process subscribes
to that channel via :func:`start_invalidation_listener` so multiple
workers converge within ~5 seconds without restart.

Redis is treated as best-effort: when the channel is unreachable the
write still commits, the audit row still lands, and a warning logs.
Consumers fall back to the TTL.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings as app_settings
from app.core.errors import AppError, ValidationFailed
from app.db.models.system_settings import SystemSetting
from app.schemas.platform_settings import (
    AuthMfaSettings,
    AuthOAuthSettings,
    AuthRegistrationSettings,
    CacheControlDefaults,
    CompactionSettings,
    EmailSmtpSettings,
    EvolverSettings,
    GeneralSettings,
    HubSettings,
    InsightsSettings,
    OAuthProvider,
    PluginsSettings,
    ProviderFailoverDefaults,
    SecuritySandboxSettings,
    SecurityShieldsSettings,
    SessionRoutingDefaults,
    SubagentBatchDefaults,
    WorkspaceDefaultsSettings,
)
from app.services import audit as audit_svc
from app.services.system_settings import (
    CuratorDefaults,
    MemoryDefaults,
    NotificationDefaults,
    RetentionSettings,
    SkillInjectionDefaults,
    SystemSettingKey,
    WorkspaceQuotaSettings,
    delete_system_setting,
    get_system_setting,
    set_system_setting,
)

log = logging.getLogger(__name__)


# ── Section catalogue ───────────────────────────────────────
class PlatformSettingsSection(StrEnum):
    GENERAL = "general"
    AUTH_REGISTRATION = "auth.registration"
    AUTH_OAUTH = "auth.oauth"
    AUTH_MFA = "auth.mfa"
    EMAIL_SMTP = "email.smtp"
    WORKSPACE_QUOTA = "workspace.quota"
    WORKSPACE_DEFAULTS = "workspace.defaults"
    SECURITY_SHIELDS = "security.shields"
    SECURITY_SANDBOX = "security.sandbox"
    EVOLVER = "evolver"
    NOTIFICATIONS = "notifications"
    PLUGINS = "plugins"
    RETENTION = "retention"
    MEMORY = "memory"
    # Appended at the tail intentionally — sibling Wave-3 milestones may
    # be adding their own sections; keeping new entries below the
    # historical block avoids merge conflicts on the diff.
    SKILL_INJECTION = "skill.injection"
    # M1.4 — Skill Curator nightly sweep defaults. Workspace overrides
    # ride ``home_config_json["curator"]``; this section seeds the
    # platform-wide fallback knobs.
    CURATOR = "curator"
    # M2.5.3 — provider failover chain defaults. Workspaces opt in via
    # ``home_config_json["providers"]["failover_enabled"]``; this
    # section seeds the platform-wide chain + cooldown knobs.
    PROVIDER_FAILOVER = "provider_failover"
    # M2.5.6 — sub-agent batch spawn defaults. Workspace overrides via
    # ``home_config_json["subagent"]`` win when set; this section seeds
    # the platform-wide batch_enabled / max_concurrent / max_batch_size
    # / max_nesting_depth knobs.
    SUBAGENT_BATCH = "subagent.batch"
    # M2.5.9 — provider-side cache marker defaults. Workspace overrides
    # ride ``home_config_json["providers"]["cache_control"]``; this
    # section seeds enabled_default / min_prompt_tokens / max
    # breakpoints / TTL plus the adaptive disable threshold + window.
    CACHE_CONTROL = "cache_control"
    # M3.1 — Skill Hub catalog defaults. Drives the federation surface:
    # whether the hub is enabled at all, the default scope a freshly
    # filed promotion lands on, and whether a workspace can pull a
    # hub pack while the M3.2 sanitizer is still being wired.
    HUB = "hub"
    # M3.6 — cross-platform session routing. Workspace overrides ride
    # ``home_config_json["session_routing"]``; this section seeds the
    # platform-wide ``cross_platform_enabled`` flag (default False so
    # existing deployments observe zero behaviour change), pairing
    # requirement, and pairing-code TTL.
    SESSION_ROUTING = "session.routing"
    # M4.3 — compaction / lineage replay defaults. Workspace overrides
    # ride ``home_config_json["compaction"]``; the future sliding-
    # window compaction layer consults ``preserve_lineage`` to decide
    # whether to stamp ``original_turns_ref`` on the summary message
    # (and ``compressed_into_summary_id`` on the originals) so the
    # M4.3 replay endpoint can resolve them. The runtime never feeds
    # this metadata into the LLM context, so flipping the knob does
    # not re-shape the M0.7 cache prefix on subsequent turns.
    COMPACTION = "compaction"
    # M4.5 — cross-session insights defaults. Workspace overrides ride
    # ``home_config_json["insights"]``; the slash command + ARQ task
    # consult these defaults to bound the lookback window and cap the
    # aux-LLM input size. ``enabled=False`` short-circuits the slash
    # command before the breaker / rate budget is consulted.
    INSIGHTS = "insights"


SECTION_SCHEMAS: dict[PlatformSettingsSection, type[BaseModel]] = {
    PlatformSettingsSection.GENERAL: GeneralSettings,
    PlatformSettingsSection.AUTH_REGISTRATION: AuthRegistrationSettings,
    PlatformSettingsSection.AUTH_OAUTH: AuthOAuthSettings,
    PlatformSettingsSection.AUTH_MFA: AuthMfaSettings,
    PlatformSettingsSection.EMAIL_SMTP: EmailSmtpSettings,
    PlatformSettingsSection.WORKSPACE_QUOTA: WorkspaceQuotaSettings,
    PlatformSettingsSection.WORKSPACE_DEFAULTS: WorkspaceDefaultsSettings,
    PlatformSettingsSection.SECURITY_SHIELDS: SecurityShieldsSettings,
    PlatformSettingsSection.SECURITY_SANDBOX: SecuritySandboxSettings,
    PlatformSettingsSection.EVOLVER: EvolverSettings,
    PlatformSettingsSection.NOTIFICATIONS: NotificationDefaults,
    PlatformSettingsSection.PLUGINS: PluginsSettings,
    PlatformSettingsSection.RETENTION: RetentionSettings,
    PlatformSettingsSection.MEMORY: MemoryDefaults,
    PlatformSettingsSection.SKILL_INJECTION: SkillInjectionDefaults,
    PlatformSettingsSection.CURATOR: CuratorDefaults,
    PlatformSettingsSection.PROVIDER_FAILOVER: ProviderFailoverDefaults,
    PlatformSettingsSection.SUBAGENT_BATCH: SubagentBatchDefaults,
    PlatformSettingsSection.CACHE_CONTROL: CacheControlDefaults,
    PlatformSettingsSection.HUB: HubSettings,
    PlatformSettingsSection.SESSION_ROUTING: SessionRoutingDefaults,
    PlatformSettingsSection.COMPACTION: CompactionSettings,
    PlatformSettingsSection.INSIGHTS: InsightsSettings,
}


# Sections backed by a single ``SystemSettingKey`` row.
# ``AUTH_REGISTRATION`` is composite — handled separately in
# :func:`_load_section` / :func:`_persist_section`.
SECTION_TO_KEY: dict[PlatformSettingsSection, SystemSettingKey] = {
    PlatformSettingsSection.GENERAL: SystemSettingKey.GENERAL,
    PlatformSettingsSection.AUTH_OAUTH: SystemSettingKey.AUTH_OAUTH,
    PlatformSettingsSection.AUTH_MFA: SystemSettingKey.AUTH_MFA,
    PlatformSettingsSection.EMAIL_SMTP: SystemSettingKey.EMAIL_SMTP,
    PlatformSettingsSection.WORKSPACE_QUOTA: SystemSettingKey.WORKSPACE_QUOTA,
    PlatformSettingsSection.WORKSPACE_DEFAULTS: SystemSettingKey.WORKSPACE_DEFAULTS,
    PlatformSettingsSection.SECURITY_SHIELDS: SystemSettingKey.SECURITY_SHIELDS,
    PlatformSettingsSection.SECURITY_SANDBOX: SystemSettingKey.SECURITY_SANDBOX,
    # M2.6 promotes ``evolver_defaults`` to canonical; the legacy
    # ``evolver`` row (M0.13 placeholder) is read once via the back-
    # compat branch in ``_load_section`` and then superseded on first
    # write.
    PlatformSettingsSection.EVOLVER: SystemSettingKey.EVOLVER_DEFAULTS,
    PlatformSettingsSection.NOTIFICATIONS: SystemSettingKey.NOTIFICATION_DEFAULTS,
    PlatformSettingsSection.PLUGINS: SystemSettingKey.PLUGINS,
    PlatformSettingsSection.RETENTION: SystemSettingKey.RETENTION,
    PlatformSettingsSection.MEMORY: SystemSettingKey.MEMORY_DEFAULTS,
    PlatformSettingsSection.SKILL_INJECTION: SystemSettingKey.SKILL_INJECTION_DEFAULTS,
    PlatformSettingsSection.CURATOR: SystemSettingKey.CURATOR_DEFAULTS,
    PlatformSettingsSection.PROVIDER_FAILOVER: SystemSettingKey.PROVIDER_FAILOVER,
    PlatformSettingsSection.SUBAGENT_BATCH: SystemSettingKey.SUBAGENT_BATCH_DEFAULTS,
    PlatformSettingsSection.CACHE_CONTROL: SystemSettingKey.CACHE_CONTROL,
    PlatformSettingsSection.HUB: SystemSettingKey.HUB,
    PlatformSettingsSection.SESSION_ROUTING: SystemSettingKey.SESSION_ROUTING_DEFAULTS,
    PlatformSettingsSection.COMPACTION: SystemSettingKey.COMPACTION,
    PlatformSettingsSection.INSIGHTS: SystemSettingKey.INSIGHTS_DEFAULTS,
}


DANGEROUS_FIELDS: dict[PlatformSettingsSection, frozenset[str]] = {
    PlatformSettingsSection.SECURITY_SANDBOX: frozenset(
        {"allow_local_execute_in_prod", "allow_ssh_backend"}
    ),
    PlatformSettingsSection.AUTH_REGISTRATION: frozenset({"mode"}),
    PlatformSettingsSection.PLUGINS: frozenset({"allow_user_plugins", "allow_unapproved_plugins"}),
}


# When the operator changes one of these sections every other platform
# admin gets an email so a compromised admin cannot quietly relax
# security posture. Sections inside :data:`EMAIL_NOTIFY_SECTIONS`
# bypass the notification cooldown.
EMAIL_NOTIFY_SECTIONS: frozenset[PlatformSettingsSection] = frozenset(
    {
        PlatformSettingsSection.SECURITY_SHIELDS,
        PlatformSettingsSection.SECURITY_SANDBOX,
        PlatformSettingsSection.AUTH_REGISTRATION,
        PlatformSettingsSection.AUTH_OAUTH,
        PlatformSettingsSection.PLUGINS,
    }
)


# Field names whose values must NEVER appear in audit metadata. The
# diff serializer replaces them with ``"***"``.
SECRET_FIELD_NAMES: frozenset[str] = frozenset(
    {"client_secret", "client_secret_ref", "password", "password_ref", "signing_root_pubkey"}
)


# Maps a section field → the ``Settings`` attribute that seeds it on
# first boot. Composite keys (``auth.oauth`` providers) are handled
# imperatively below; the table only covers flat 1:1 mappings.
ENV_FIELD_MAPPING: dict[PlatformSettingsSection, dict[str, str]] = {
    PlatformSettingsSection.AUTH_REGISTRATION: {
        "rate_limit_per_minute": "AUTH_REGISTER_RATE_LIMIT",
    },
    PlatformSettingsSection.EMAIL_SMTP: {
        # ``SMTP_*`` variables don't exist in :class:`Settings` yet — the
        # bootstrap helper reads them off ``os.environ`` directly so an
        # operator's existing ``.env`` migrates without a config schema
        # bump. Keys present here are read for documentation / UI hints.
        "host": "SMTP_HOST",
        "port": "SMTP_PORT",
        "username": "SMTP_USERNAME",
        "password_ref": "SMTP_PASSWORD",
        "from_address": "SMTP_FROM",
        "use_tls": "SMTP_USE_TLS",
    },
    PlatformSettingsSection.GENERAL: {
        "default_locale": "APP_DEFAULT_LOCALE",
        "default_timezone": "APP_TIMEZONE",
        "site_name": "APP_NAME",
    },
    PlatformSettingsSection.SECURITY_SANDBOX: {
        "allow_local_execute_in_prod": "SANDBOX_LOCAL_EXECUTE_PROD",
    },
}


# ── Errors ──────────────────────────────────────────────────
# Mirrors the project-wide ``app.core.errors`` naming: the AppError
# subclasses don't carry an ``Error`` suffix because the frontend
# matches on the stable ``code`` rather than the class name.
class DangerousChangeRequiresConfirmation(AppError):
    """Raised when an admin tries to change a flagged field without
    ``confirmed_dangerous=True``. The frontend handles this code by
    rendering an extra confirmation dialog.
    """

    code = "platform_settings.dangerous_change_requires_confirmation"
    default_status = 400


class UnknownPlatformSection(AppError):
    code = "platform_settings.unknown_section"
    default_status = 404


# ── Cache ───────────────────────────────────────────────────
@dataclass(slots=True)
class _CacheEntry:
    value: BaseModel
    expires_at: float


_cache: dict[PlatformSettingsSection, _CacheEntry] = {}
_CACHE_TTL_SECONDS = 30
PLATFORM_SETTINGS_CHANNEL = "platform_settings:invalidated"


def _now() -> float:
    return datetime.now(UTC).timestamp()


def _cache_set(section: PlatformSettingsSection, value: BaseModel) -> None:
    _cache[section] = _CacheEntry(value=value, expires_at=_now() + _CACHE_TTL_SECONDS)


def _cache_get(section: PlatformSettingsSection) -> BaseModel | None:
    entry = _cache.get(section)
    if entry is None:
        return None
    if entry.expires_at < _now():
        _cache.pop(section, None)
        return None
    return entry.value


def invalidate_local(section: PlatformSettingsSection | None = None) -> None:
    """Drop the in-process cache entry for a section (or all sections)."""
    if section is None:
        _cache.clear()
        return
    _cache.pop(section, None)


# ── DTOs ────────────────────────────────────────────────────
@dataclass(slots=True)
class SectionMeta:
    section: PlatformSettingsSection
    value: BaseModel
    env_overrides: list[str]
    db_present: bool
    last_modified_at: datetime | None


# ── Read path ───────────────────────────────────────────────
def _section_or_raise(section: str | PlatformSettingsSection) -> PlatformSettingsSection:
    if isinstance(section, PlatformSettingsSection):
        return section
    try:
        return PlatformSettingsSection(section)
    except ValueError as exc:
        raise UnknownPlatformSection(
            f"unknown platform section: {section!r}",
            extras={"section": section},
        ) from exc


async def _load_auth_registration(db: AsyncSession) -> tuple[dict[str, Any], bool]:
    """Combine the four legacy keys into one section payload.

    Returns ``(payload, db_present)`` where ``db_present`` is True iff
    at least one of the constituent rows exists in the DB. This makes
    the admin "reset" affordance honest — clicking Reset clears every
    backing row.
    """
    mode_row = await _has_row(db, SystemSettingKey.REGISTRATION_MODE)
    verify_row = await _has_row(db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION)
    rate_row = await _has_row(db, SystemSettingKey.AUTH_REGISTER_RATE_LIMIT_PER_MINUTE)
    invite_row = await _has_row(db, SystemSettingKey.INVITATION_EXPIRY_DAYS)

    payload = {
        "mode": await get_system_setting(
            db, SystemSettingKey.REGISTRATION_MODE, default="open_personal"
        ),
        "require_email_verification": bool(
            await get_system_setting(
                db, SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION, default=False
            )
        ),
        "rate_limit_per_minute": int(
            await get_system_setting(
                db,
                SystemSettingKey.AUTH_REGISTER_RATE_LIMIT_PER_MINUTE,
                default=app_settings.AUTH_REGISTER_RATE_LIMIT,
            )
        ),
        "invitation_expiry_days": int(
            await get_system_setting(db, SystemSettingKey.INVITATION_EXPIRY_DAYS, default=30)
        ),
    }
    db_present = any([mode_row, verify_row, rate_row, invite_row])
    return payload, db_present


async def _has_row(db: AsyncSession, key: SystemSettingKey) -> bool:
    row = (
        await db.execute(select(SystemSetting).where(SystemSetting.key == key.value))
    ).scalar_one_or_none()
    return row is not None


async def _load_section(
    db: AsyncSession, *, section: PlatformSettingsSection
) -> tuple[dict[str, Any], bool]:
    """Pull the raw payload + db_presence flag for a section."""
    if section == PlatformSettingsSection.AUTH_REGISTRATION:
        return await _load_auth_registration(db)
    key = SECTION_TO_KEY[section]
    raw = await get_system_setting(db, key, default=None)
    if raw is None and section == PlatformSettingsSection.EVOLVER:
        # M2.6 back-compat: the legacy ``evolver`` row (M0.13
        # placeholder) is the source for any deployment that ran
        # M0.13 → M1.x without writing the new defaults yet. The
        # EvolverSettings ``model_validator(mode="before")`` translates
        # legacy field names so the schema layer never has to branch.
        legacy = await get_system_setting(db, SystemSettingKey.EVOLVER, default=None)
        if isinstance(legacy, dict):
            return legacy, True
    if raw is None:
        # Schema default takes over downstream.
        schema = SECTION_SCHEMAS[section]
        return schema().model_dump(mode="json"), False
    if not isinstance(raw, dict | list):
        raw = {}
    return raw if isinstance(raw, dict) else {"items": raw}, True


def _detect_env_overrides(section: PlatformSettingsSection, value: BaseModel) -> list[str]:
    """Return field names whose current value matches an active env var.

    Used purely for the admin UI badge — it tells the operator "the
    SMTP host you see came from your .env, not from a manual save".
    The check is intentionally string-based: when the env var is set
    AND the field value equals the env value, we surface it. If the
    operator overwrites the field via the admin UI the row in
    ``system_settings`` overrides; the env value still gets surfaced
    only when DB and env happen to agree on the same string.
    """
    env_map = ENV_FIELD_MAPPING.get(section, {})
    if not env_map:
        if section == PlatformSettingsSection.AUTH_OAUTH:
            return _detect_env_overrides_oauth(value)
        return []
    overrides: list[str] = []
    serialized = value.model_dump(mode="json")
    for field, env_key in env_map.items():
        env_value = os.environ.get(env_key)
        if env_value is None or env_value == "":
            continue
        current = serialized.get(field)
        if str(current) == env_value or str(current).lower() == env_value.lower():
            overrides.append(field)
    return overrides


def _detect_env_overrides_oauth(value: BaseModel) -> list[str]:
    overrides: list[str] = []
    serialized = value.model_dump(mode="json")
    providers = serialized.get("providers") or []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        env_prefix = f"OAUTH_{provider.get('name', '').upper()}"
        client_id_env = os.environ.get(f"{env_prefix}_CLIENT_ID")
        if client_id_env and provider.get("client_id") == client_id_env:
            overrides.append(f"providers[{provider['name']}].client_id")
    return overrides


async def _last_modified_at(
    db: AsyncSession, *, section: PlatformSettingsSection
) -> datetime | None:
    """Read ``updated_at`` of the underlying row(s); None when absent."""
    if section == PlatformSettingsSection.AUTH_REGISTRATION:
        keys = [
            SystemSettingKey.REGISTRATION_MODE.value,
            SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION.value,
            SystemSettingKey.AUTH_REGISTER_RATE_LIMIT_PER_MINUTE.value,
            SystemSettingKey.INVITATION_EXPIRY_DAYS.value,
        ]
    else:
        keys = [SECTION_TO_KEY[section].value]
    rows = (
        (await db.execute(select(SystemSetting).where(SystemSetting.key.in_(keys)))).scalars().all()
    )
    timestamps = [getattr(r, "updated_at", None) for r in rows if r is not None]
    timestamps = [t for t in timestamps if t is not None]
    if not timestamps:
        return None
    return max(timestamps)


async def get_section(db: AsyncSession, *, section: PlatformSettingsSection | str) -> BaseModel:
    """Cache > DB > env-bootstrap > schema defaults."""
    sec = _section_or_raise(section)
    cached = _cache_get(sec)
    if cached is not None:
        return cached
    raw, _ = await _load_section(db, section=sec)
    schema = SECTION_SCHEMAS[sec]
    try:
        value = schema.model_validate(raw)
    except ValidationError as exc:
        log.warning(
            "platform_settings: stored row for %s failed validation (%s); falling back to defaults",
            sec.value,
            exc,
        )
        value = schema()
    _cache_set(sec, value)
    return value


async def get_section_with_meta(
    db: AsyncSession, *, section: PlatformSettingsSection | str
) -> SectionMeta:
    sec = _section_or_raise(section)
    raw, db_present = await _load_section(db, section=sec)
    schema = SECTION_SCHEMAS[sec]
    try:
        value = schema.model_validate(raw)
    except ValidationError:
        value = schema()
    last_at = await _last_modified_at(db, section=sec)
    return SectionMeta(
        section=sec,
        value=value,
        env_overrides=_detect_env_overrides(sec, value),
        db_present=db_present,
        last_modified_at=last_at,
    )


# ── Write path ──────────────────────────────────────────────
def _redact_for_audit(payload: dict[str, Any]) -> dict[str, Any]:
    """Shallow + one-level deep walk replacing secret-named fields with ``***``."""
    out: dict[str, Any] = {}
    for k, v in payload.items():
        if k in SECRET_FIELD_NAMES and v not in (None, ""):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _redact_for_audit(v)
        elif isinstance(v, list):
            out[k] = [_redact_for_audit(item) if isinstance(item, dict) else item for item in v]
        else:
            out[k] = v
    return out


def _diff_payloads(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Return ``{field: {"old": ..., "new": ...}}`` for changed leaves only.

    Used for audit metadata — secret values are pre-redacted by the
    caller so this helper only sees ``"***"`` placeholders.
    """
    changed: dict[str, Any] = {}
    keys = set(old) | set(new)
    for k in keys:
        if old.get(k) != new.get(k):
            changed[k] = {"old": old.get(k), "new": new.get(k)}
    return changed


async def _persist_auth_registration(db: AsyncSession, *, value: AuthRegistrationSettings) -> None:
    await set_system_setting(db, SystemSettingKey.REGISTRATION_MODE, value.mode)
    await set_system_setting(
        db,
        SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION,
        bool(value.require_email_verification),
    )
    await set_system_setting(
        db,
        SystemSettingKey.AUTH_REGISTER_RATE_LIMIT_PER_MINUTE,
        int(value.rate_limit_per_minute),
    )
    await set_system_setting(
        db,
        SystemSettingKey.INVITATION_EXPIRY_DAYS,
        int(value.invitation_expiry_days),
    )


async def _persist_section(
    db: AsyncSession,
    *,
    section: PlatformSettingsSection,
    value: BaseModel,
) -> None:
    if section == PlatformSettingsSection.AUTH_REGISTRATION:
        await _persist_auth_registration(
            db, value=AuthRegistrationSettings.model_validate(value.model_dump())
        )
        return
    key = SECTION_TO_KEY[section]
    await set_system_setting(db, key, value.model_dump(mode="json"))


async def _delete_section_rows(db: AsyncSession, *, section: PlatformSettingsSection) -> None:
    if section == PlatformSettingsSection.AUTH_REGISTRATION:
        for key in (
            SystemSettingKey.REGISTRATION_MODE,
            SystemSettingKey.AUTH_REQUIRE_EMAIL_VERIFICATION,
            SystemSettingKey.AUTH_REGISTER_RATE_LIMIT_PER_MINUTE,
            SystemSettingKey.INVITATION_EXPIRY_DAYS,
        ):
            await delete_system_setting(db, key)
        return
    await delete_system_setting(db, SECTION_TO_KEY[section])


def _detect_dangerous_changes(
    section: PlatformSettingsSection,
    *,
    old: dict[str, Any],
    new: dict[str, Any],
) -> list[str]:
    flagged = DANGEROUS_FIELDS.get(section, frozenset())
    if not flagged:
        return []
    changed: list[str] = []
    for field in flagged:
        old_val = old.get(field)
        new_val = new.get(field)
        # ``mode = closed`` is the only dangerous transition for auth.registration.
        if section == PlatformSettingsSection.AUTH_REGISTRATION and field == "mode":
            if new_val == "closed" and old_val != "closed":
                changed.append(field)
            continue
        # Plugins / sandbox: dangerous only when transitioning False -> True.
        if isinstance(new_val, bool) and isinstance(old_val, bool):
            if new_val and not old_val:
                changed.append(field)
            continue
        if old_val != new_val:
            changed.append(field)
    return changed


async def update_section(
    db: AsyncSession,
    *,
    section: PlatformSettingsSection | str,
    payload: dict[str, Any],
    actor_identity_id: uuid.UUID,
    request: Request | None = None,
    confirmed_dangerous: bool = False,
) -> BaseModel:
    """Validate, persist, audit, invalidate, and notify.

    Caller owns the transaction; this helper does **not** commit. The
    invalidation publish happens after the function returns so a
    concurrent reader on another worker sees the new value as soon as
    the route layer commits.
    """
    sec = _section_or_raise(section)
    schema = SECTION_SCHEMAS[sec]
    try:
        new_value = schema.model_validate(payload)
    except ValidationError as exc:
        raise ValidationFailed(
            "section_payload_invalid",
            code="platform_settings.invalid_payload",
            extras={"errors": exc.errors()},
        ) from exc

    old_raw, _ = await _load_section(db, section=sec)
    new_raw = new_value.model_dump(mode="json")
    dangerous_changed = _detect_dangerous_changes(sec, old=old_raw, new=new_raw)
    if dangerous_changed and not confirmed_dangerous:
        raise DangerousChangeRequiresConfirmation(
            "dangerous_change_requires_confirmation",
            extras={"section": sec.value, "fields": sorted(dangerous_changed)},
        )

    await _persist_section(db, section=sec, value=new_value)

    diff = _diff_payloads(_redact_for_audit(old_raw), _redact_for_audit(new_raw))
    await audit_svc.record(
        db,
        action="platform_settings.updated",
        actor_identity_id=actor_identity_id,
        workspace_id=None,
        resource_type="platform_settings",
        resource_id=None,
        summary=f"updated {sec.value}",
        metadata={"section": sec.value, "diff": diff},
        request=request,
    )
    if dangerous_changed:
        await audit_svc.record(
            db,
            action="platform_settings.dangerous_change",
            actor_identity_id=actor_identity_id,
            workspace_id=None,
            resource_type="platform_settings",
            resource_id=None,
            summary=f"dangerous change in {sec.value}",
            metadata={"section": sec.value, "fields": sorted(dangerous_changed)},
            request=request,
        )

    invalidate_local(sec)
    _cache_set(sec, new_value)

    _schedule_side_effects(sec, actor_identity_id)
    return new_value


def _schedule_side_effects(section: PlatformSettingsSection, actor_identity_id: uuid.UUID) -> None:
    """Fire-and-forget the post-update fan-out.

    Wrapped in ``try/except`` so a missing event loop (e.g. inside a
    sync unit test that calls ``update_section`` directly) doesn't
    surface as ``RuntimeError`` — the caller's transaction must
    succeed regardless of cache-broadcast availability.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    # Fire-and-forget: the result is never awaited because cache
    # invalidation must not delay the caller's commit. Errors inside
    # the task are caught + logged by ``_post_update_side_effects``.
    loop.create_task(  # noqa: RUF006
        _post_update_side_effects(
            section=section,
            actor_identity_id=actor_identity_id,
            email_notify=section in EMAIL_NOTIFY_SECTIONS,
        )
    )


async def _post_update_side_effects(
    *,
    section: PlatformSettingsSection,
    actor_identity_id: uuid.UUID,
    email_notify: bool,
) -> None:
    try:
        await publish_invalidation(section)
    except Exception:  # pragma: no cover - fail-open
        log.exception("publish_invalidation failed for %s", section.value)
    try:
        await _emit_changed_notification(
            section=section,
            actor_identity_id=actor_identity_id,
            email_notify=email_notify,
        )
    except Exception:  # pragma: no cover - fail-open
        log.exception("platform_settings.changed emit failed for %s", section.value)
    if section == PlatformSettingsSection.EMAIL_SMTP:
        try:
            from app.db.session import get_session_factory
            from app.services.email_transport import (
                reload_email_transport_from_settings,
            )

            factory = get_session_factory()
            async with factory() as fresh:
                await reload_email_transport_from_settings(fresh)
        except Exception:  # pragma: no cover - fail-open
            log.exception("email transport reload failed")


async def _emit_changed_notification(
    *,
    section: PlatformSettingsSection,
    actor_identity_id: uuid.UUID,
    email_notify: bool,
) -> None:
    from app.db.session import get_session_factory
    from app.services.notification_events import EVENT_REGISTRY, emit_event

    descriptor = EVENT_REGISTRY.get("platform_settings.changed")
    if descriptor is None:
        return

    factory = get_session_factory()
    async with factory() as fresh:
        await emit_event(
            fresh,
            event_key="platform_settings.changed",
            workspace_id=None,
            actor_identity_id=actor_identity_id,
            payload={
                "section": section.value,
                "resource_type": "platform_settings",
            },
            cooldown_resource_id=None if email_notify else section.value,
        )
        # When the section is in :data:`EMAIL_NOTIFY_SECTIONS` we enqueue
        # a direct email to every other platform admin so the security
        # critical change is in their inbox even when the registry's
        # default channels later drop EMAIL — security mail bypasses
        # cooldown and is keyed by section + day.
        if email_notify:
            await _enqueue_admin_email(
                fresh,
                section=section,
                actor_identity_id=actor_identity_id,
                descriptor=descriptor,
            )
        await fresh.commit()


async def _enqueue_admin_email(
    db: AsyncSession,
    *,
    section: PlatformSettingsSection,
    actor_identity_id: uuid.UUID,
    descriptor: Any,
) -> None:
    """Push a critical-security email to every other platform admin.

    Bypasses the per-event cooldown because security-relevant settings
    must be visible immediately. Idempotency is handled by the email
    job's own ``idempotency_key``: we feed a section + day-bucket seed
    so retries inside the same day collapse to one email.
    """
    from datetime import date

    from app.db.models.identity import (
        Identity,
        IdentityStatus,
        PlatformRole,
    )
    from app.worker import queue as queue_svc

    stmt = (
        select(Identity)
        .where(Identity.platform_role == PlatformRole.PLATFORM_ADMIN)
        .where(Identity.status == IdentityStatus.ACTIVE)
        .where(Identity.deleted_at.is_(None))
        .where(Identity.id != actor_identity_id)
    )
    admins = list((await db.execute(stmt)).scalars().all())
    if not admins:
        return
    today = date.today().isoformat()
    for admin in admins:
        await queue_svc.enqueue(
            "send_email_notification",
            {
                "event_key": "platform_settings.changed",
                "to_email": admin.email,
                "to_identity_id": str(admin.id),
                "title_key": descriptor.title_key,
                "message_key": descriptor.message_key,
                "payload": {
                    "section": section.value,
                    "actor_identity_id": str(actor_identity_id),
                },
                "urgency": "critical",
                "workspace_id": None,
                "idempotency_key": f"platform_settings.changed:{section.value}:{today}",
                "subject_fallback": f"[SenHarness] platform setting changed: {section.value}",
                "body_fallback": (
                    f"A platform admin changed the {section.value!r} section. "
                    "Review the audit log if this was unexpected."
                ),
            },
        )


async def reset_section(
    db: AsyncSession,
    *,
    section: PlatformSettingsSection | str,
    actor_identity_id: uuid.UUID,
    request: Request | None = None,
) -> BaseModel:
    """Drop the DB row(s); next read returns env / schema defaults."""
    sec = _section_or_raise(section)
    await _delete_section_rows(db, section=sec)
    await audit_svc.record(
        db,
        action="platform_settings.reset",
        actor_identity_id=actor_identity_id,
        workspace_id=None,
        resource_type="platform_settings",
        resource_id=None,
        summary=f"reset {sec.value}",
        metadata={"section": sec.value},
        request=request,
    )
    invalidate_local(sec)
    _schedule_side_effects(sec, actor_identity_id)
    return await get_section(db, section=sec)


# ── Bootstrap from env ──────────────────────────────────────
def _coerce_env(raw: str, target_type: type) -> Any:
    """Best-effort coercion for env-string → expected Python type."""
    if target_type is bool:
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    if target_type is int:
        try:
            return int(raw)
        except ValueError:
            return None
    return raw


def _build_smtp_payload_from_env() -> dict[str, Any] | None:
    """Construct an :class:`EmailSmtpSettings` payload from raw env vars.

    Returns ``None`` when no SMTP env is set so the caller can skip
    seeding the row entirely (and the default ``enabled=False`` row
    keeps the LogEmailTransport path active).
    """
    host = os.environ.get("SMTP_HOST")
    if not host:
        return None
    return {
        "enabled": _coerce_env(os.environ.get("SMTP_ENABLED", "true"), bool),
        "host": host,
        "port": _coerce_env(os.environ.get("SMTP_PORT", "587"), int) or 587,
        "username": os.environ.get("SMTP_USERNAME") or None,
        "password_ref": os.environ.get("SMTP_PASSWORD_REF") or None,
        "from_address": os.environ.get("SMTP_FROM") or None,
        "use_tls": _coerce_env(os.environ.get("SMTP_USE_TLS", "true"), bool),
    }


def _build_oauth_payload_from_env() -> dict[str, Any] | None:
    """Build an OAuth provider list from ``OAUTH_<NAME>_*`` env vars.

    Returns ``None`` if no provider env is configured.
    """
    providers: list[dict[str, Any]] = []
    for name in ("github", "google", "microsoft", "feishu"):
        client_id = os.environ.get(f"OAUTH_{name.upper()}_CLIENT_ID")
        if not client_id:
            continue
        secret_ref = os.environ.get(f"OAUTH_{name.upper()}_CLIENT_SECRET_REF")
        providers.append(
            {
                "name": name,
                "enabled": True,
                "client_id": client_id,
                "client_secret_ref": secret_ref,
                "scopes": [],
            }
        )
    if not providers:
        return None
    return {"providers": providers, "auto_link_existing_email": True}


async def bootstrap_from_env_if_empty(db: AsyncSession) -> dict[str, int]:
    """Seed the ``system_settings`` table from env vars on first boot.

    Returns ``{section: fields_seeded_count}`` for the audit row. Skips
    sections whose row already exists so an operator can flip a value
    via the admin UI and not have it overwritten by a stale .env on
    the next restart.

    The function is intentionally idempotent — it can run on every
    startup; it only writes when the DB row is absent.
    """
    seeded: dict[str, int] = {}

    for section, env_map in ENV_FIELD_MAPPING.items():
        raw, db_present = await _load_section(db, section=section)
        if db_present:
            continue
        schema = SECTION_SCHEMAS[section]
        # Start from the current (default) payload; only overlay env-
        # supplied fields so unspecified fields keep their schema default.
        merged: dict[str, Any] = dict(raw)
        if section == PlatformSettingsSection.AUTH_REGISTRATION:
            merged = AuthRegistrationSettings.model_validate(merged).model_dump(mode="json")
        count = 0
        for field, env_key in env_map.items():
            settings_value = getattr(app_settings, env_key, None)
            env_value: Any
            if settings_value not in (None, ""):
                env_value = settings_value
            else:
                env_value = os.environ.get(env_key)
            if env_value is None or env_value == "":
                continue
            field_info = schema.model_fields.get(field)
            target_type = field_info.annotation if field_info else str
            coerced = (
                _coerce_env(env_value, target_type) if isinstance(env_value, str) else env_value
            )
            if coerced is None:
                continue
            merged[field] = coerced
            count += 1
        if count == 0:
            continue
        try:
            value = schema.model_validate(merged)
        except ValidationError as exc:
            log.warning(
                "bootstrap: env values for %s failed validation (%s); skipping",
                section.value,
                exc,
            )
            continue
        await _persist_section(db, section=section, value=value)
        seeded[section.value] = count

    smtp_payload = _build_smtp_payload_from_env()
    if smtp_payload is not None:
        _, db_present = await _load_section(db, section=PlatformSettingsSection.EMAIL_SMTP)
        if not db_present:
            try:
                value = EmailSmtpSettings.model_validate(smtp_payload)
                await _persist_section(db, section=PlatformSettingsSection.EMAIL_SMTP, value=value)
                seeded[PlatformSettingsSection.EMAIL_SMTP.value] = seeded.get(
                    PlatformSettingsSection.EMAIL_SMTP.value, 0
                ) + len([k for k, v in smtp_payload.items() if v is not None])
            except ValidationError as exc:
                log.warning("bootstrap: SMTP env invalid (%s); skipping", exc)

    oauth_payload = _build_oauth_payload_from_env()
    if oauth_payload is not None:
        _, db_present = await _load_section(db, section=PlatformSettingsSection.AUTH_OAUTH)
        if not db_present:
            try:
                value = AuthOAuthSettings.model_validate(oauth_payload)
                await _persist_section(db, section=PlatformSettingsSection.AUTH_OAUTH, value=value)
                seeded[PlatformSettingsSection.AUTH_OAUTH.value] = len(oauth_payload["providers"])
            except ValidationError as exc:
                log.warning("bootstrap: OAuth env invalid (%s); skipping", exc)

    if seeded:
        await audit_svc.record(
            db,
            action="platform_settings.bootstrapped_from_env",
            actor_identity_id=None,
            workspace_id=None,
            resource_type="platform_settings",
            resource_id=None,
            summary=(
                f"seeded {sum(seeded.values())} fields across {len(seeded)} sections from env"
            ),
            metadata={"sections": seeded},
        )
        await db.commit()
    invalidate_local()
    return seeded


# ── Redis pub/sub ───────────────────────────────────────────
_listener_task: asyncio.Task[None] | None = None


async def publish_invalidation(section: PlatformSettingsSection) -> None:
    """Best-effort broadcast on the invalidation channel."""
    try:
        from app.core.rate_limit import get_redis  # late import to avoid cycle

        r = get_redis()
        await r.publish(PLATFORM_SETTINGS_CHANNEL, section.value)
    except Exception as exc:  # pragma: no cover - fail-open
        log.info(
            "platform_settings: redis publish failed (%s); other workers will refresh after TTL",
            exc,
        )


async def _consume_invalidations(channel: Any) -> None:
    async for message in channel.listen():
        if message is None:
            continue
        if message.get("type") != "message":
            continue
        raw = message.get("data")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            sec = PlatformSettingsSection(raw)
        except ValueError:
            log.warning("platform_settings: ignored invalidation for unknown section %r", raw)
            continue
        invalidate_local(sec)
        if sec == PlatformSettingsSection.EMAIL_SMTP:
            await _reload_email_transport_safe()


async def _reload_email_transport_safe() -> None:
    try:
        from app.db.session import get_session_factory
        from app.services.email_transport import (
            reload_email_transport_from_settings,
        )

        factory = get_session_factory()
        async with factory() as fresh:
            await reload_email_transport_from_settings(fresh)
    except Exception:  # pragma: no cover - fail-open
        log.exception("email transport reload (post-invalidation) failed")


async def start_invalidation_listener() -> asyncio.Task[None] | None:
    """Subscribe to the Redis invalidation channel.

    Idempotent: returns the existing task if already started. Returns
    ``None`` when Redis is not reachable; the cache TTL is the
    fallback and a single warning is logged.
    """
    global _listener_task
    if _listener_task is not None and not _listener_task.done():
        return _listener_task

    async def _runner() -> None:
        try:
            from app.core.rate_limit import get_redis

            r = get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe(PLATFORM_SETTINGS_CHANNEL)
            log.info("platform_settings: subscribed to %s", PLATFORM_SETTINGS_CHANNEL)
            try:
                await _consume_invalidations(pubsub)
            finally:
                with suppress(Exception):
                    await pubsub.unsubscribe(PLATFORM_SETTINGS_CHANNEL)
                with suppress(Exception):
                    await pubsub.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "platform_settings: listener crashed (%s); cache TTL takes over",
                exc,
            )

    _listener_task = asyncio.create_task(_runner(), name="platform_settings_listener")
    return _listener_task


async def stop_invalidation_listener() -> None:
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await _listener_task
    _listener_task = None


# ── Public catalog helper for the API layer ─────────────────
def list_sections() -> list[PlatformSettingsSection]:
    """Stable iteration order matching the admin sidebar."""
    return list(PlatformSettingsSection)


def section_schema_json(section: PlatformSettingsSection | str) -> dict[str, Any]:
    """JSON Schema for the schema-driven form generator."""
    sec = _section_or_raise(section)
    return SECTION_SCHEMAS[sec].model_json_schema()


__all__ = [
    "DANGEROUS_FIELDS",
    "EMAIL_NOTIFY_SECTIONS",
    "ENV_FIELD_MAPPING",
    "PLATFORM_SETTINGS_CHANNEL",
    "SECTION_SCHEMAS",
    "SECTION_TO_KEY",
    "DangerousChangeRequiresConfirmation",
    "PlatformSettingsSection",
    "SectionMeta",
    "UnknownPlatformSection",
    "bootstrap_from_env_if_empty",
    "get_section",
    "get_section_with_meta",
    "invalidate_local",
    "list_sections",
    "publish_invalidation",
    "reset_section",
    "section_schema_json",
    "start_invalidation_listener",
    "stop_invalidation_listener",
    "update_section",
]


# Silence unused imports (kept for downstream re-export convenience).
_ = (OAuthProvider,)
