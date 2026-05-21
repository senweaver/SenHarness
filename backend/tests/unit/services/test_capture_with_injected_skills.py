"""Unit tests for the M1.5 injected-skill capture wiring.

These tests pin the contract of two helpers that bridge the runtime's
in-memory pack id stash (``NativeBackend._injected_skill_ids``) and the
DB-backed capture path:

* ``_read_injected_skill_ids(backend, run_id)`` — returns ``[]`` for any
  backend that doesn't expose ``get_injected_skill_ids`` (OpenClaw and
  remote adapters fall in this bucket) and swallows lookup exceptions
  so a misbehaving runtime never breaks artifact capture.
* ``_record_skill_injection_usage(...)`` — never raises; on failure it
  emits ``audit_events(action="skill.usage_recording_failed")`` so the
  rollup pipeline has a breadcrumb.

The DB-backed end-to-end coverage lives in
``backend/tests/integration/test_skill_injection_end_to_end.py``.
"""

from __future__ import annotations

import uuid


class _NativeLikeBackend:
    """Mimics ``NativeBackend.get_injected_skill_ids`` shape."""

    def __init__(self, mapping: dict[uuid.UUID, list[uuid.UUID]]) -> None:
        self._mapping = mapping
        self.calls: list[uuid.UUID] = []

    def get_injected_skill_ids(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        self.calls.append(run_id)
        return list(self._mapping.get(run_id, []))


class _RaisingBackend:
    def get_injected_skill_ids(self, run_id: uuid.UUID) -> list[uuid.UUID]:
        raise RuntimeError("boom")


class _OpenClawLikeBackend:
    """Backend without ``get_injected_skill_ids`` (e.g. remote adapter)."""


def test_read_injected_returns_pack_ids_from_native_backend() -> None:
    from app.api.v1 import sessions as sessions_mod

    run_id = uuid.uuid4()
    pack_a = uuid.uuid4()
    pack_b = uuid.uuid4()
    backend = _NativeLikeBackend({run_id: [pack_a, pack_b]})

    ids = sessions_mod._read_injected_skill_ids(backend, run_id)

    assert ids == [pack_a, pack_b]
    assert backend.calls == [run_id]


def test_read_injected_returns_empty_for_non_native_backend() -> None:
    from app.api.v1 import sessions as sessions_mod

    backend = _OpenClawLikeBackend()

    ids = sessions_mod._read_injected_skill_ids(backend, uuid.uuid4())

    assert ids == []


def test_read_injected_returns_empty_when_backend_is_none() -> None:
    from app.api.v1 import sessions as sessions_mod

    assert sessions_mod._read_injected_skill_ids(None, uuid.uuid4()) == []


def test_read_injected_swallows_lookup_exceptions() -> None:
    from app.api.v1 import sessions as sessions_mod

    backend = _RaisingBackend()

    ids = sessions_mod._read_injected_skill_ids(backend, uuid.uuid4())

    assert ids == []


def test_read_injected_returns_empty_for_unknown_run_id() -> None:
    from app.api.v1 import sessions as sessions_mod

    backend = _NativeLikeBackend({uuid.uuid4(): [uuid.uuid4()]})

    assert sessions_mod._read_injected_skill_ids(backend, uuid.uuid4()) == []


def test_agent_runner_read_helper_mirrors_sessions() -> None:
    """Both call paths share the same defensive contract.

    The two helpers are intentionally independent (the agent_runner one
    operates on the caller's ``db`` while the sessions one runs in its
    own short-lived factory) so we pin both surfaces here.
    """
    from app.services import agent_runner

    pack = uuid.uuid4()
    run_id = uuid.uuid4()
    backend = _NativeLikeBackend({run_id: [pack]})

    assert agent_runner._read_injected_skill_ids(backend, run_id) == [pack]
    assert agent_runner._read_injected_skill_ids(None, run_id) == []
    assert (
        agent_runner._read_injected_skill_ids(_OpenClawLikeBackend(), run_id)
        == []
    )
    assert agent_runner._read_injected_skill_ids(_RaisingBackend(), run_id) == []
