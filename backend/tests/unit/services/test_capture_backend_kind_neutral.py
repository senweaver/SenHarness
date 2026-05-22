"""M1.5 backend-kind neutrality.

NativeBackend exposes ``get_injected_skill_ids`` as a ClassVar-backed
introspection hook. OpenClaw and other remote adapters do not — they
forward ``RunRequest.skills`` over the wire and have no in-process
record of which packs the remote runtime actually injected.

The capture path must therefore never assume the hook exists. These
tests pin the contract: any backend without the hook produces an
empty pack id list, which feeds through the rest of the M1.5 pipeline
(no SkillUsage rows, no batch audit) without raising.
"""

from __future__ import annotations

import uuid


class _OpenClawLikeBackend:
    """Simulates a remote adapter (no introspection hook)."""

    backend_kind = "remote"


class _NativeLikeBackendNoMatch:
    """Hook is present but the run isn't in the stash (defensive guard)."""

    backend_kind = "native"

    def get_injected_skill_ids(self, _run_id: uuid.UUID) -> list[uuid.UUID]:
        return []


class _BadlyTypedBackend:
    """Hook returns a non-list — defensive coercion via ``list(...)``."""

    backend_kind = "native"

    def get_injected_skill_ids(self, _run_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
        return (uuid.uuid4(),)


def test_remote_backend_yields_empty_list_via_sessions_helper() -> None:
    from app.api.v1 import sessions as sessions_mod

    assert sessions_mod._read_injected_skill_ids(_OpenClawLikeBackend(), uuid.uuid4()) == []


def test_remote_backend_yields_empty_list_via_agent_runner_helper() -> None:
    from app.services import agent_runner

    assert agent_runner._read_injected_skill_ids(_OpenClawLikeBackend(), uuid.uuid4()) == []


def test_native_backend_with_no_match_yields_empty_list() -> None:
    from app.api.v1 import sessions as sessions_mod
    from app.services import agent_runner

    backend = _NativeLikeBackendNoMatch()
    run_id = uuid.uuid4()
    assert sessions_mod._read_injected_skill_ids(backend, run_id) == []
    assert agent_runner._read_injected_skill_ids(backend, run_id) == []


def test_tuple_return_is_coerced_to_list() -> None:
    """Defensive: backends that return a tuple still feed downstream
    list-typed APIs (``record_usage_batch`` annotates ``list[UUID]``).
    """
    from app.api.v1 import sessions as sessions_mod

    backend = _BadlyTypedBackend()
    ids = sessions_mod._read_injected_skill_ids(backend, uuid.uuid4())
    assert isinstance(ids, list)
    assert len(ids) == 1
