"""Typed business errors. All carry a stable `code` usable for i18n lookup."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


class AppError(HTTPException):
    """Base application error with a machine-readable `code`."""

    code: str = "app.error"
    default_status: int = status.HTTP_400_BAD_REQUEST

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(status_code=status_code or self.default_status, detail=detail or self.code)
        self.code = code or self.code
        self.extras = extras or {}


class NotFound(AppError):
    code = "app.not_found"
    default_status = status.HTTP_404_NOT_FOUND


class PermissionDenied(AppError):
    code = "app.permission_denied"
    default_status = status.HTTP_403_FORBIDDEN


class Unauthorized(AppError):
    code = "app.unauthorized"
    default_status = status.HTTP_401_UNAUTHORIZED


class Conflict(AppError):
    code = "app.conflict"
    default_status = status.HTTP_409_CONFLICT


class ValidationFailed(AppError):
    code = "app.validation_failed"
    default_status = status.HTTP_422_UNPROCESSABLE_ENTITY


class RateLimited(AppError):
    code = "app.rate_limited"
    default_status = status.HTTP_429_TOO_MANY_REQUESTS


class ServiceUnavailable(AppError):
    code = "app.service_unavailable"
    default_status = status.HTTP_503_SERVICE_UNAVAILABLE


class RegistrationClosed(AppError):
    code = "auth.registration_closed"
    default_status = status.HTTP_403_FORBIDDEN


class InvitationRequired(AppError):
    code = "auth.invitation_required"
    default_status = status.HTTP_400_BAD_REQUEST


class EmailNotVerified(AppError):
    code = "auth.email_unverified"
    default_status = status.HTTP_403_FORBIDDEN


class QuotaExceeded(AppError):
    code = "workspace.quota_exceeded"
    default_status = status.HTTP_403_FORBIDDEN


class CreationRateLimited(AppError):
    code = "workspace.creation_rate_limit"
    default_status = status.HTTP_429_TOO_MANY_REQUESTS


class CreationNotPermitted(AppError):
    code = "workspace.creation_not_permitted"
    default_status = status.HTTP_403_FORBIDDEN


class SlugTombstoned(AppError):
    code = "workspace.slug_tombstoned"
    default_status = status.HTTP_409_CONFLICT


class SkillSlugTombstoned(AppError):
    """Raised when ``POST /skills/packs`` is asked to reuse a slug that
    was previously tombstoned in this workspace. Slugs entering
    :class:`~app.db.models.tombstone_slug.TombstoneSlug` are permanent
    — see roadmap principle 10 ("never delete, only archive").
    """

    code = "skill.slug_tombstoned"
    default_status = status.HTTP_409_CONFLICT


# ─── M0.7 — cache-aware mutation invariant ─────────────────────
class ImmediateMemoryNotPermitted(AppError):
    """Raised when an agent or caller asks for ``effective="now"`` /
    ``force=True`` and the workspace has not opted in via
    ``home_config_json["memory"]["allow_immediate"]``. The default-deny
    posture preserves the prompt cache by deferring all memory writes
    to the next session boundary.
    """

    code = "memory.immediate_not_permitted"
    default_status = status.HTTP_403_FORBIDDEN


class MemoryHardCapExceeded(AppError):
    """Raised when a memory write would push the workspace's always-on
    memory total beyond ``memory.always_on_max_chars``. Hard caps keep
    the system prompt below the model's effective context budget after
    persona, tools, and skills are composed.
    """

    code = "memory.hard_cap_exceeded"
    default_status = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


# ─── M2.5.10 — SSH sandbox typed errors ─────────────────────────
class SandboxKindDisabled(AppError):
    """Raised when ``kind="ssh"`` is requested but the platform admin
    has not enabled ``security.sandbox.allow_ssh_backend``. Default
    posture is fail-closed: SSH talks to remote hosts with arbitrary
    shell access, so it requires an explicit platform-level opt-in
    before any workspace can wire it up.
    """

    code = "sandbox.kind_disabled"
    default_status = status.HTTP_403_FORBIDDEN


class SshConfigInvalid(AppError):
    """Raised when an SSH sandbox config violates a non-Pydantic
    invariant — production + ``execute=True`` + empty
    ``command_allowlist``, etc. Pydantic's own ``ValidationError``
    covers field-level shape; this carries the cross-field /
    environment-aware checks.
    """

    code = "sandbox.ssh_config_invalid"
    default_status = status.HTTP_400_BAD_REQUEST


class SshCommandRejected(AppError):
    """Raised when ``run_command`` rejects a command before any network
    activity — typically because the command is not in the
    ``command_allowlist``. Distinct from :class:`SshCommandDenied`
    which represents a human approval refusal.
    """

    code = "sandbox.ssh_command_rejected"
    default_status = status.HTTP_403_FORBIDDEN


class SshCommandDenied(AppError):
    """Raised when an SSH command was refused by the approval gate —
    explicit deny by an operator or TTL expiry. Distinct from
    :class:`SshCommandRejected` so the chat UI / audit feed can
    distinguish "policy said no" from "human said no".
    """

    code = "sandbox.ssh_command_denied"
    default_status = status.HTTP_403_FORBIDDEN


class SshKnownHostsMismatch(AppError):
    """Raised when the remote host key does not match the configured
    ``known_hosts_pin``. Always fail-closed; the admin must update the
    pin via the workspace settings UI before the connection can land.
    """

    code = "sandbox.ssh_known_hosts_mismatch"
    default_status = status.HTTP_403_FORBIDDEN


# ─── M3.1 — Skill Hub catalog ────────────────────────────────
class HubScopePermissionDenied(AppError):
    """Raised when a non-platform-admin tries to file a PLATFORM-scope
    hub action (promote / state transition). TENANT-scope actions
    flow through the workspace admin path and never raise this.
    """

    code = "hub.scope_permission_denied"
    default_status = status.HTTP_403_FORBIDDEN


class HubInvalidStateTransition(AppError):
    """Edge not allowed by the hub state machine."""

    code = "hub.invalid_transition"
    default_status = status.HTTP_409_CONFLICT


class HubTerminalState(AppError):
    """Pack is already TOMBSTONE — no further transitions allowed."""

    code = "hub.terminal_state"
    default_status = status.HTTP_409_CONFLICT


class HubSlugTombstoned(AppError):
    """Raised when a hub-side promote is asked to reuse a slug that
    was previously tombstoned for the same ``(scope, tenant_id)``.
    Slugs in TOMBSTONE state are permanent (roadmap principle 10).
    """

    code = "hub.slug_tombstoned"
    default_status = status.HTTP_409_CONFLICT


class HubDisabled(AppError):
    """Raised when ``HubSettings.enabled`` is False but a request is
    asking the hub surface to do work."""

    code = "hub.disabled"
    default_status = status.HTTP_403_FORBIDDEN


# ─── M3.4 — agent profile cross-workspace stats gate ─────────
class CrossWorkspaceStatsForbidden(AppError):
    """Raised when a non-platform-admin tries to read the
    ``cross_workspace_stats_json`` slice of an agent profile. The
    workspace-scoped read route must use the alternative shape that
    omits the field; this error fires only on the dedicated
    ``/admin/agents/{agent_id}/profile/cross-workspace`` route when
    the caller is not a platform admin.
    """

    code = "agent_profile.cross_workspace_forbidden"
    default_status = status.HTTP_403_FORBIDDEN
