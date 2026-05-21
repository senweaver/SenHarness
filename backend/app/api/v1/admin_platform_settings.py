"""Platform-admin surface for the unified settings page (M0.13).

Seven endpoints, all gated by ``platform_admin`` + per-bucket
rate limit:

* ``GET    /admin/platform-settings``                              — list all 14 sections + meta
* ``GET    /admin/platform-settings/{section}``                    — single section + meta
* ``GET    /admin/platform-settings/{section}/schema``             — JSON Schema for the form generator
* ``PUT    /admin/platform-settings/{section}``                    — update (Pydantic-validated, dangerous-confirm)
* ``POST   /admin/platform-settings/{section}/reset``              — reset to schema defaults
* ``POST   /admin/platform-settings/email.smtp/test``              — dry-run SMTP send (no persistence)
* ``POST   /admin/platform-settings/auth.oauth/{provider}/test``   — OAuth metadata smoke-test

Every mutation writes ``platform_settings.*`` audit and (when the
section is in :data:`EMAIL_NOTIFY_SECTIONS`) emails every other
platform admin.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import DBSession
from app.api.v1.admin import AdminGate
from app.core.errors import ValidationFailed
from app.core.rate_limit import rate_limit
from app.db.models.identity import Identity
from app.services import platform_settings as svc
from app.services.email_transport import (
    EmailDispatchResult,
    SmtpEmailTransport,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/platform-settings", tags=["admin", "settings"])


# ── DTOs ────────────────────────────────────────────────────
class SectionRead(BaseModel):
    section: str
    value: dict[str, Any]
    env_overrides: list[str] = Field(default_factory=list)
    db_present: bool
    last_modified_at: datetime | None = None
    dangerous_fields: list[str] = Field(default_factory=list)
    is_email_notify: bool = False


class SectionListOut(BaseModel):
    sections: list[SectionRead]


class SectionUpdateIn(BaseModel):
    value: dict[str, Any]
    confirmed_dangerous: bool = False


class SectionResetOut(BaseModel):
    section: str
    value: dict[str, Any]


class SmtpTestIn(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    from_address: EmailStr
    use_tls: bool = True
    to: EmailStr | None = None


class SmtpTestOut(BaseModel):
    ok: bool
    transport: str
    message_id: str | None = None
    error: str | None = None


class OAuthTestOut(BaseModel):
    ok: bool
    provider: str
    metadata_url: str | None = None
    error: str | None = None


# ── Helpers ─────────────────────────────────────────────────
def _serialize_section(meta: svc.SectionMeta) -> SectionRead:
    return SectionRead(
        section=meta.section.value,
        value=meta.value.model_dump(mode="json"),
        env_overrides=meta.env_overrides,
        db_present=meta.db_present,
        last_modified_at=meta.last_modified_at,
        dangerous_fields=sorted(svc.DANGEROUS_FIELDS.get(meta.section, frozenset())),
        is_email_notify=meta.section in svc.EMAIL_NOTIFY_SECTIONS,
    )


# ── Routes ──────────────────────────────────────────────────
@router.get(
    "",
    response_model=SectionListOut,
    dependencies=[
        Depends(rate_limit("platform_settings_read", limit=30, period_seconds=60)),
    ],
)
async def list_sections(
    db: DBSession, _admin: Identity = AdminGate
) -> SectionListOut:
    sections: list[SectionRead] = []
    for sec in svc.list_sections():
        meta = await svc.get_section_with_meta(db, section=sec)
        sections.append(_serialize_section(meta))
    return SectionListOut(sections=sections)


@router.get(
    "/{section}",
    response_model=SectionRead,
    dependencies=[
        Depends(rate_limit("platform_settings_read", limit=30, period_seconds=60)),
    ],
)
async def get_section(
    section: str = Path(..., description="Section key e.g. 'general' / 'auth.registration'"),
    *,
    db: DBSession,
    _admin: Identity = AdminGate,
) -> SectionRead:
    meta = await svc.get_section_with_meta(db, section=section)
    return _serialize_section(meta)


@router.get(
    "/{section}/schema",
    dependencies=[
        Depends(
            rate_limit("platform_settings_schema_read", limit=60, period_seconds=60)
        ),
    ],
)
async def get_section_schema(
    section: str = Path(...),
    *,
    _admin: Identity = AdminGate,
) -> dict[str, Any]:
    return svc.section_schema_json(section)


@router.put(
    "/{section}",
    response_model=SectionRead,
    dependencies=[
        Depends(rate_limit("platform_settings_write", limit=20, period_seconds=60)),
    ],
)
async def update_section(
    body: SectionUpdateIn,
    request: Request,
    db: DBSession,
    section: str = Path(...),
    admin: Identity = AdminGate,
) -> SectionRead:
    await svc.update_section(
        db,
        section=section,
        payload=body.value,
        actor_identity_id=admin.id,
        request=request,
        confirmed_dangerous=body.confirmed_dangerous,
    )
    await db.commit()
    meta = await svc.get_section_with_meta(db, section=section)
    return _serialize_section(meta)


@router.post(
    "/{section}/reset",
    response_model=SectionResetOut,
    dependencies=[
        Depends(rate_limit("platform_settings_reset", limit=5, period_seconds=60)),
    ],
)
async def reset_section(
    request: Request,
    db: DBSession,
    section: str = Path(...),
    admin: Identity = AdminGate,
) -> SectionResetOut:
    value = await svc.reset_section(
        db,
        section=section,
        actor_identity_id=admin.id,
        request=request,
    )
    await db.commit()
    return SectionResetOut(
        section=svc._section_or_raise(section).value,
        value=value.model_dump(mode="json"),
    )


@router.post(
    "/email.smtp/test",
    response_model=SmtpTestOut,
    dependencies=[
        Depends(
            rate_limit("platform_settings_smtp_test", limit=5, period_seconds=300)
        ),
    ],
)
async def test_smtp(
    body: SmtpTestIn,
    request: Request,
    db: DBSession,
    admin: Identity = AdminGate,
) -> SmtpTestOut:
    """Dry-run SMTP send using the posted payload — never persists.

    The recipient defaults to the calling admin's verified email so a
    typo in ``from_address`` becomes obvious. The handler swaps the
    process-wide transport singleton temporarily? No — it constructs
    a one-shot :class:`SmtpEmailTransport` so concurrent admins on a
    different page are not affected.
    """
    transport = SmtpEmailTransport(
        host=body.host,
        port=body.port,
        username=body.username,
        password=body.password,
        from_address=str(body.from_address),
        use_tls=body.use_tls,
    )
    to = str(body.to) if body.to else admin.email
    result: EmailDispatchResult = await transport.send(
        to=to,
        subject="SenHarness SMTP test",
        body_text=(
            "If you see this message your SMTP credentials are valid. "
            "Click 'Save' on the admin page to persist them."
        ),
    )
    from app.services import audit as audit_svc

    await audit_svc.record(
        db,
        action="platform_settings.smtp_test",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="platform_settings",
        resource_id=None,
        summary=f"SMTP test ok={result.ok}",
        metadata={
            "host": body.host,
            "port": body.port,
            "ok": result.ok,
            "error": result.error,
        },
        request=request,
    )
    await db.commit()
    return SmtpTestOut(
        ok=result.ok,
        transport=result.transport,
        message_id=result.message_id,
        error=result.error,
    )


_OAUTH_METADATA_ENDPOINTS = {
    "github": "https://api.github.com/",
    "google": "https://accounts.google.com/.well-known/openid-configuration",
    "microsoft": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration",
    "feishu": "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
}


@router.post(
    "/auth.oauth/{provider}/test",
    response_model=OAuthTestOut,
    dependencies=[
        Depends(
            rate_limit("platform_settings_oauth_test", limit=5, period_seconds=300)
        ),
    ],
)
async def test_oauth(
    request: Request,
    db: DBSession,
    provider: Literal["github", "google", "microsoft", "feishu"] = Path(...),
    admin: Identity = AdminGate,
) -> OAuthTestOut:
    """Smoke-test the provider's discovery / metadata endpoint.

    Full consent dance is out of scope until M3 SSO; this handler
    validates that the provider is reachable from the backend (network
    + DNS + TLS) which catches the most common misconfigurations.
    """
    endpoint = _OAUTH_METADATA_ENDPOINTS.get(provider)
    if endpoint is None:
        raise ValidationFailed(
            f"unsupported_oauth_provider:{provider}",
            code="platform_settings.oauth_provider_unknown",
        )
    ok = False
    error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(endpoint)
            ok = response.status_code in (200, 401)  # GitHub root returns 200
            if not ok:
                error = f"http_{response.status_code}"
    except Exception as exc:
        error = f"unreachable:{exc}"
    from app.services import audit as audit_svc

    await audit_svc.record(
        db,
        action="platform_settings.oauth_test",
        actor_identity_id=admin.id,
        workspace_id=None,
        resource_type="platform_settings",
        resource_id=None,
        summary=f"OAuth {provider} test ok={ok}",
        metadata={"provider": provider, "ok": ok, "error": error},
        request=request,
    )
    await db.commit()
    return OAuthTestOut(
        ok=ok, provider=provider, metadata_url=endpoint, error=error
    )
