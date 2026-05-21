"""Schemas for the M2.5.7 Two-Model-ID pattern.

Two layers:

* :class:`ServedAliasMap` — what lives at
  ``workspace.home_config_json["providers"]["served_alias_map"]``.
  Maps the client-facing brand name (``served_model_name``) to the
  upstream model id passed into ``RunRequest.model_override``.
* :class:`ResolvedServedModel` — the runtime envelope returned by
  :func:`app.services.served_model.resolve_served_model`. Carries
  the served name, the upstream id, and a structured
  ``matched_via`` provenance string for audit logs.
* :class:`ServedAliasUpsertIn` / :class:`ServedAliasOut` /
  :class:`ServedAliasListOut` — DTOs for the workspace settings
  REST surface.

Validation invariants (kept tight on purpose so the runner never
sees a half-formed mapping):

* ``served_name`` is 1..120 chars, ``[A-Za-z0-9_./:-]`` plus the
  agent_kind sentinel pattern. The set is deliberately broader than
  ``model_id`` so brand names like ``ws-fast`` / ``acme/chat-2025``
  both fit.
* ``upstream`` is 1..200 chars, free-form. The runner will pass it
  to :func:`app.agents.kernels.model_client.parse_override`; if it
  isn't ``provider:model`` shaped the override falls through to the
  workspace's default provider — see ``docs/extensions-and-governance.md``
  (Provider routing → Two-model-ID pattern).
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Allow letters / digits / dot / slash / colon / underscore / dash.
# Brand names like ``ws-fast``, ``acme/chat-2025``, or even
# ``deepseek:deepseek-chat`` (when the served name happens to
# match the upstream id) are all OK.
_SERVED_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,120}$")
_UPSTREAM_PATTERN = re.compile(r"^[A-Za-z0-9._:/-]{1,200}$")


def validate_served_name(value: str) -> str:
    cleaned = (value or "").strip()
    if not _SERVED_NAME_PATTERN.match(cleaned):
        raise ValueError(
            "served_name must be 1-120 chars matching [A-Za-z0-9._:/-]"
        )
    return cleaned


def validate_upstream(value: str) -> str:
    cleaned = (value or "").strip()
    if not _UPSTREAM_PATTERN.match(cleaned):
        raise ValueError(
            "upstream must be 1-200 chars matching [A-Za-z0-9._:/-]"
        )
    return cleaned


MatchedVia = Literal["agent_field", "workspace_alias", "fallback"]


class ServedAliasMap(BaseModel):
    """Workspace-scoped alias map: served_name → upstream model id."""

    aliases: dict[str, str] = Field(default_factory=dict)

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for raw_key, raw_val in value.items():
            key = validate_served_name(raw_key)
            val = validate_upstream(raw_val)
            out[key] = val
        return out


class ResolvedServedModel(BaseModel):
    """Runtime envelope returned by :func:`resolve_served_model`."""

    served_name: str
    upstream: str
    matched_via: MatchedVia


class ServedAliasUpsertIn(BaseModel):
    """``PUT /workspaces/{id}/settings/served-aliases/{served}`` body."""

    upstream: str

    @field_validator("upstream")
    @classmethod
    def _v(cls, value: str) -> str:
        return validate_upstream(value)


class ServedAliasOut(BaseModel):
    served_name: str
    upstream: str


class ServedAliasListOut(BaseModel):
    aliases: list[ServedAliasOut]


class ServedModelEntry(BaseModel):
    """One entry in :class:`ServedModelListOut`."""

    served_name: str
    upstream: str | None = None
    matched_via: MatchedVia
    agent_id: uuid.UUID | None = None


class ServedModelListOut(BaseModel):
    """Wire shape for the OpenAI-compatible ``/v1/models`` listing."""

    object: Literal["list"] = "list"
    data: list[ServedModelEntry]
