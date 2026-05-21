"""Personal-workspace slug allocation for self-registration.

Pure-async, no transaction management. The auth service calls
:func:`reserve_personal_workspace_slug` inside a larger transaction
that may roll back; the slug computation never persists anything on
its own.
"""

from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workspace import Workspace
from app.services.system_settings import SystemSettingKey, get_system_setting

_BASE_RESERVED_SLUGS: frozenset[str] = frozenset(
    {
        "admin",
        "platform",
        "api",
        "hub",
        "system",
        "public",
        "settings",
        "login",
        "register",
        "oauth",
        "auth",
        "_health",
        "metrics",
        "internal",
        "static",
        "assets",
        "files",
        "uploads",
        "downloads",
        "ws",
        "websocket",
        "callback",
        "logout",
        "me",
        "users",
        "workspaces",
        "agents",
        "channels",
        "flows",
        "skills",
        "memory",
        "memories",
        "approvals",
        "audit",
        "notifications",
        "test",
        "debug",
        "root",
        "owner",
        "moderator",
        "support",
        "help",
        "docs",
        "blog",
        "status",
        "billing",
        "pricing",
        "terms",
        "privacy",
        "senharness-system",
    }
)

_MAX_SLUG_LEN = 60
_MAX_LINEAR_SUFFIX = 9
_MAX_RANDOM_TRIES = 5

_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_DASH_COLLAPSE_RE = re.compile(r"-+")


def _sanitize_local_part(email: str) -> str:
    """Email local-part -> slug-safe lowercase. Strips +tags; collapses non [a-z0-9-]."""
    local = email.split("@", 1)[0] if "@" in email else email
    local = local.lower()
    if "+" in local:
        local = local.split("+", 1)[0]
    local = local.replace(".", "-")
    local = _INVALID_CHARS_RE.sub("-", local)
    local = _DASH_COLLAPSE_RE.sub("-", local).strip("-")
    if not local:
        return "user"
    if len(local) > _MAX_SLUG_LEN:
        local = local[:_MAX_SLUG_LEN].strip("-") or "user"
    return local


def _random_suffix() -> str:
    return secrets.token_hex(3)


async def get_reserved_slugs(db: AsyncSession) -> frozenset[str]:
    """Merge base reserved slugs with platform-admin extras from system_settings."""
    extras_raw = await get_system_setting(
        db, SystemSettingKey.RESERVED_WORKSPACE_SLUGS_EXTRA, default=[]
    )
    if not isinstance(extras_raw, list):
        extras_raw = []
    extras: set[str] = set()
    for item in extras_raw:
        if not item:
            continue
        token = str(item).strip().lower()
        if token:
            extras.add(token)
    return frozenset(_BASE_RESERVED_SLUGS | extras)


async def _slug_taken(db: AsyncSession, slug: str) -> bool:
    """True when the slug is unavailable for new use.

    Considers a slug taken when *either* an active workspace currently
    holds it, *or* a past workspace's slug was tombstoned by
    ``DELETE /workspaces/{id}`` (M0.12). The tombstone branch keeps a
    malicious user from cycling create→delete→re-create on the same
    slug to bypass the per-identity quota or to squat on a slug that
    used to belong to an organization.
    """
    from app.services import workspace_quota as quota_svc

    stmt = (
        select(Workspace.id)
        .where(Workspace.slug == slug)
        .where(Workspace.deleted_at.is_(None))
        .limit(1)
    )
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        return True
    return await quota_svc.is_slug_tombstoned(db, slug=slug)


async def reserve_personal_workspace_slug(
    db: AsyncSession, *, email: str
) -> tuple[str, bool]:
    """Compute a slug for a freshly-registered user's personal workspace.

    Returns ``(slug, used_random_suffix)``. ``used_random_suffix`` is True
    only when the linear ``base-2..base-9`` probe was exhausted and the
    final slug carries a random 6-hex tail. The frontend uses that flag
    to surface a one-shot "your workspace got renamed to X" toast.

    Slug uniqueness is checked across active workspaces only (deleted_at
    IS NULL). Reserved slugs (base set + platform-admin extras) skip the
    linear probe and go straight to the random tail.
    """
    reserved = await get_reserved_slugs(db)
    base = _sanitize_local_part(email)

    if base not in reserved:
        if not await _slug_taken(db, base):
            return base, False

        for suffix in range(2, _MAX_LINEAR_SUFFIX + 1):
            candidate = f"{base}-{suffix}"
            if len(candidate) > _MAX_SLUG_LEN:
                trimmed_base = base[: _MAX_SLUG_LEN - len(f"-{suffix}")].rstrip("-") or "user"
                candidate = f"{trimmed_base}-{suffix}"
            if candidate in reserved:
                continue
            if not await _slug_taken(db, candidate):
                return candidate, False

    for _ in range(_MAX_RANDOM_TRIES):
        suffix = _random_suffix()
        trimmed_base = base
        if len(trimmed_base) > _MAX_SLUG_LEN - len(suffix) - 1:
            trimmed_base = trimmed_base[: _MAX_SLUG_LEN - len(suffix) - 1].rstrip("-") or "user"
        candidate = f"{trimmed_base}-{suffix}"
        if candidate in reserved:
            continue
        if not await _slug_taken(db, candidate):
            return candidate, True

    final = f"u-{secrets.token_hex(6)}"
    return final, True
