"""Wire DTOs that mirror the platform ``system_settings`` payloads.

Re-exports the validation models defined in the service layer so the
API + admin UI (M0.13) can validate updates against the same shape
without taking a service-layer dependency from the schema layer.

M0.13 adds nine new section schemas under
:mod:`app.schemas.platform_settings`; they're re-exported here so
external callers (tests, admin endpoints, frontend type generation)
can import every section through one module.
"""

from __future__ import annotations

from app.schemas.platform_settings import (
    AuthMfaSettings,
    AuthOAuthSettings,
    AuthRegistrationSettings,
    EmailSmtpSettings,
    EvolverSettings,
    GeneralSettings,
    OAuthProvider,
    PluginsSettings,
    SecuritySandboxSettings,
    SecurityShieldsSettings,
    WorkspaceDefaultsSettings,
)
from app.services.system_settings import (
    CuratorDefaults,
    MemoryDefaults,
    NotificationDefaults,
    RetentionSettings,
    SkillInjectionDefaults,
    WorkspaceQuotaSettings,
)

__all__ = [
    "AuthMfaSettings",
    "AuthOAuthSettings",
    "AuthRegistrationSettings",
    "CuratorDefaults",
    "EmailSmtpSettings",
    "EvolverSettings",
    "GeneralSettings",
    "MemoryDefaults",
    "NotificationDefaults",
    "OAuthProvider",
    "PluginsSettings",
    "RetentionSettings",
    "SecuritySandboxSettings",
    "SecurityShieldsSettings",
    "SkillInjectionDefaults",
    "WorkspaceDefaultsSettings",
    "WorkspaceQuotaSettings",
]
