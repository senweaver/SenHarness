"""Pydantic schemas for the unified platform-admin settings page (M0.13).

The 14 sections rendered by ``/admin/settings/<section>`` are validated
against the schemas exported here. Five sections re-use models that
landed in earlier milestones (M0.7 ``MemoryDefaults``, M0.10
``NotificationDefaults``, M0.11 ``RetentionSettings``, M0.12
``WorkspaceQuotaSettings``, M0.9 ``RegistrationMode`` aggregated into
``AuthRegistrationSettings``); the nine new sections live in this
folder so the section registry can iterate one set of imports.

Re-defining a schema in M0.13 would risk drift against the live caller;
we always import the canonical version from its owning module.
"""

from __future__ import annotations

from app.schemas.platform_settings.auth_mfa import AuthMfaSettings
from app.schemas.platform_settings.auth_oauth import (
    AuthOAuthSettings,
    OAuthProvider,
)
from app.schemas.platform_settings.auth_registration import (
    AuthRegistrationSettings,
)
from app.schemas.platform_settings.cache_control import (
    CacheControlDefaults,
)
from app.schemas.platform_settings.compaction import CompactionSettings
from app.schemas.platform_settings.email_smtp import EmailSmtpSettings
from app.schemas.platform_settings.evolver import EvolverSettings
from app.schemas.platform_settings.general import GeneralSettings
from app.schemas.platform_settings.hub import HubSettings
from app.schemas.platform_settings.insights import InsightsSettings
from app.schemas.platform_settings.plugins import PluginsSettings
from app.schemas.platform_settings.provider_failover import (
    ProviderFailoverDefaults,
)
from app.schemas.platform_settings.security_sandbox import (
    SecuritySandboxSettings,
)
from app.schemas.platform_settings.security_shields import (
    SecurityShieldsSettings,
)
from app.schemas.platform_settings.session_routing import (
    SessionRoutingDefaults,
)
from app.schemas.platform_settings.subagent_batch import (
    SubagentBatchDefaults,
)
from app.schemas.platform_settings.workspace_defaults import (
    WorkspaceDefaultsSettings,
)

__all__ = [
    "AuthMfaSettings",
    "AuthOAuthSettings",
    "AuthRegistrationSettings",
    "CacheControlDefaults",
    "CompactionSettings",
    "EmailSmtpSettings",
    "EvolverSettings",
    "GeneralSettings",
    "HubSettings",
    "InsightsSettings",
    "OAuthProvider",
    "PluginsSettings",
    "ProviderFailoverDefaults",
    "SecuritySandboxSettings",
    "SecurityShieldsSettings",
    "SessionRoutingDefaults",
    "SubagentBatchDefaults",
    "WorkspaceDefaultsSettings",
]
