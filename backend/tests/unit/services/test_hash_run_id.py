"""Unit: ``hash_run_id`` / ``hash_source_run_ids`` (M3.2).

The hash collapses each ``source_run_id`` to a salted SHA-256 prefix.
The contract:

* deterministic: same (run_id, salt) → same hex
* salted by workspace_id: two distinct workspaces produce different
  digests for the *same* UUID, so a hub audit can't correlate
* exactly 16 hex characters (64 bits)
* accepts both ``uuid.UUID`` and string inputs
* preserves order; duplicate inputs survive as duplicate hashes
"""

from __future__ import annotations

import hashlib
import re
import uuid

from app.services.skill_sanitize import hash_run_id, hash_source_run_ids

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def test_hash_run_id_deterministic_same_input() -> None:
    run_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    a = hash_run_id(run_id, salt="ws")
    b = hash_run_id(run_id, salt="ws")
    assert a == b


def test_hash_run_id_returns_16_hex() -> None:
    digest = hash_run_id(uuid.uuid4(), salt="anything")
    assert _HEX16.match(digest), digest


def test_hash_run_id_accepts_string_form() -> None:
    rid = uuid.uuid4()
    a = hash_run_id(rid, salt="ws")
    b = hash_run_id(str(rid), salt="ws")
    assert a == b


def test_different_salts_produce_different_hashes() -> None:
    rid = uuid.uuid4()
    a = hash_run_id(rid, salt="ws-a")
    b = hash_run_id(rid, salt="ws-b")
    assert a != b


def test_hash_source_run_ids_uses_workspace_id_as_salt() -> None:
    rid = uuid.uuid4()
    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    [a] = hash_source_run_ids([rid], workspace_id=ws_a)
    [b] = hash_source_run_ids([rid], workspace_id=ws_b)
    assert a != b
    expected_a = hashlib.sha256((str(ws_a) + str(rid)).encode("utf-8")).hexdigest()[:16]
    assert a == expected_a


def test_hash_source_run_ids_preserves_order() -> None:
    ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    out = hash_source_run_ids(ids, workspace_id=uuid.uuid4())
    assert len(out) == 3
    assert all(_HEX16.match(h) for h in out)


def test_hash_source_run_ids_duplicates_preserved() -> None:
    rid = uuid.uuid4()
    ws = uuid.uuid4()
    out = hash_source_run_ids([rid, rid, rid], workspace_id=ws)
    assert len(out) == 3
    assert out[0] == out[1] == out[2]


def test_hash_source_run_ids_empty_list_returns_empty() -> None:
    assert hash_source_run_ids([], workspace_id=uuid.uuid4()) == []
