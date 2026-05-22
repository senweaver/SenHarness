"""Evolver platform / workspace settings (M2.1 + M2.7 + M2.6).

Single Pydantic schema for the self-evolution loop. The same shape
backs both the platform-default row (``system_settings.evolver_defaults``,
edited by platform admins via ``/admin/settings/evolver``) and the
per-workspace override at ``workspace.home_config_json["evolver"]``;
the workspace override layer wins on a field-by-field basis (see
:func:`app.services.evolver_config.get_workspace_evolver_config`).

Backward compatibility
----------------------

The original M0.13 placeholder shipped two fields
(``workspace_can_enable``, ``platform_aux_model_recommendation``) on
the legacy ``system_settings.evolver`` row. M2.6 replaces the schema
entirely; a ``model_validator(mode="before")`` translates legacy
payloads into the new shape so the service layer never has to branch:

* ``workspace_can_enable`` → discarded (deferred to admin: pre-M2 the
  flag never gated anything on the runtime side; the new ``enabled``
  field is the explicit per-workspace switch).
* ``platform_aux_model_recommendation`` → ``aux_model_evolver``.

After the first write through M2.6, the legacy fields are silently
dropped and the new shape becomes canonical.

Defaults
--------

``enabled = False`` is intentional: M2.7 propose verbs file
:class:`~app.db.models.approval.Approval` rows but do not mutate skill
state directly, so an admin must opt their workspace in before the
evolver agent (M2.2) can run. The roadmap's principle 1 + 11 — never
mutate without an audit + always require explicit opt-in for autonomy
— land here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class EvolverApprovalTtlDays(BaseModel):
    """Per-resource Approval TTL, all values in days.

    Mirrors the roadmap "Approval TTL strategy" table — ``create`` /
    ``patch`` / ``edit`` / ``write_file`` carry the longer 14-day
    review window because the proposal body is rich and human review
    takes longer; ``delete`` / ``remove_file`` are destructive and
    intentionally short (7 days) so an unattended workspace doesn't
    accumulate bit-rotten archive proposals.
    """

    skill_pack_create: int = Field(ge=1, le=90, default=14)
    skill_pack_patch: int = Field(ge=1, le=90, default=14)
    skill_pack_edit: int = Field(ge=1, le=90, default=14)
    skill_pack_delete: int = Field(ge=1, le=90, default=7)
    skill_pack_write_file: int = Field(ge=1, le=90, default=14)
    skill_pack_remove_file: int = Field(ge=1, le=90, default=7)
    # M2.8 — agent self-scheduling cronjob proposals. Shorter than the
    # rich content verbs because the proposal body is small (name +
    # schedule + prompt template) and an unattended workspace should
    # not accumulate stale schedule requests.
    flow_create: int = Field(ge=1, le=90, default=7)


class EvolverAutoVerifier(BaseModel):
    """M2.4 verifier knobs (consumed once M2.4 lands).

    The M2.7 propose path doesn't currently invoke the verifier — the
    candidate version state machine is ``PROPOSED`` → admin-approved →
    ``ACTIVE``. M2.4 adds the ``VALIDATING`` step; these knobs are
    parked here so the M2.6 admin form is forward-stable.
    """

    enabled: bool = True
    min_score_delta: float = Field(ge=0.0, le=1.0, default=0.05)
    min_replay_artifacts: int = Field(ge=1, le=50, default=3)


class EvolverSettings(BaseModel):
    """Workspace + platform-level self-evolution config.

    Setting :attr:`enabled` to ``True`` is the operator's explicit
    opt-in; M2.7 propose verbs short-circuit with a structured
    rejection (``code='evolver.disabled'``) when the workspace
    override resolves to disabled.
    """

    enabled: bool = False
    engine: Literal["workflow", "agent"] = "workflow"
    publish_mode: Literal["approval_required", "auto_after_validation"] = "approval_required"
    min_artifacts_per_evolution: int = Field(ge=1, le=100, default=5)
    auto_verifier: EvolverAutoVerifier = Field(default_factory=EvolverAutoVerifier)
    approval_ttl_days: EvolverApprovalTtlDays = Field(default_factory=EvolverApprovalTtlDays)
    aux_model_evolver: str | None = Field(default=None, max_length=120)
    evolver_breaker_strikes: int = Field(ge=1, le=20, default=5)
    evolver_breaker_window_seconds: int = Field(ge=60, le=3600, default=300)
    evolver_rate_per_minute: int = Field(ge=1, le=600, default=10)

    @model_validator(mode="before")
    @classmethod
    def _absorb_legacy_keys(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        legacy_aux = out.pop("platform_aux_model_recommendation", None)
        if legacy_aux is not None and "aux_model_evolver" not in out:
            out["aux_model_evolver"] = legacy_aux
        out.pop("workspace_can_enable", None)
        return out
