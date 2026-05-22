"""Unit tests for personal workspace slug allocation (M0.9).

The pure-string sanitizer can be tested without touching the DB; the
async ``reserve_personal_workspace_slug`` helper needs a Postgres
session because it inspects the ``workspaces`` table.
"""

from __future__ import annotations

import uuid

from app.services import workspace as workspace_svc
from app.services.personal_workspace import (
    _BASE_RESERVED_SLUGS,
    _sanitize_local_part,
    reserve_personal_workspace_slug,
)
from app.services.system_settings import (
    SystemSettingKey,
    set_system_setting,
)


def test_sanitize_strips_plus_tags_and_dots():
    assert _sanitize_local_part("john.doe+work@example.com") == "john-doe"


def test_sanitize_keeps_alphanumeric_and_hyphen():
    assert _sanitize_local_part("alice_99-test@ex.com") == "alice-99-test"


def test_sanitize_falls_back_to_user_when_empty():
    assert _sanitize_local_part("@example.com") == "user"
    assert _sanitize_local_part("___+@example.com") == "user"


def test_sanitize_truncates_overlong():
    long_local = "a" * 200
    out = _sanitize_local_part(f"{long_local}@example.com")
    assert len(out) <= 60
    assert out.startswith("a")


def test_sanitize_lowers_uppercase():
    assert _sanitize_local_part("Bob.Smith@Example.COM") == "bob-smith"


def test_reserved_slug_constants_lowercase():
    for slug in _BASE_RESERVED_SLUGS:
        assert slug == slug.lower(), f"reserved slug not normalised: {slug}"


async def test_reserve_returns_base_when_free(db_session):
    slug, used_random = await reserve_personal_workspace_slug(
        db_session, email=f"alice-{uuid.uuid4().hex[:6]}@example.com"
    )
    assert slug.startswith("alice-")
    assert used_random is False


async def test_reserve_appends_linear_suffix_on_conflict(db_session, identity):
    base_seed = f"slugtest{uuid.uuid4().hex[:6]}"
    await workspace_svc.create_workspace(
        db_session,
        name="Existing",
        slug=base_seed,
        owner_identity_id=identity.id,
    )
    await db_session.flush()

    slug, used_random = await reserve_personal_workspace_slug(
        db_session, email=f"{base_seed}@example.com"
    )
    assert slug == f"{base_seed}-2"
    assert used_random is False


async def test_reserve_walks_to_third_suffix(db_session, identity):
    base_seed = f"slugtri{uuid.uuid4().hex[:6]}"
    for taken in (base_seed, f"{base_seed}-2"):
        await workspace_svc.create_workspace(
            db_session,
            name=f"existing {taken}",
            slug=taken,
            owner_identity_id=identity.id,
        )
    await db_session.flush()

    slug, used_random = await reserve_personal_workspace_slug(
        db_session, email=f"{base_seed}@example.com"
    )
    assert slug == f"{base_seed}-3"
    assert used_random is False


async def test_reserved_slug_jumps_to_random_suffix(db_session):
    slug, used_random = await reserve_personal_workspace_slug(db_session, email="admin@example.com")
    assert slug.startswith("admin-")
    assert used_random is True
    suffix = slug.split("-", 1)[1]
    assert len(suffix) == 6
    assert all(c in "0123456789abcdef" for c in suffix)


async def test_extra_reserved_slugs_from_system_settings(db_session):
    extra = f"forbidden{uuid.uuid4().hex[:4]}"
    await set_system_setting(
        db_session,
        SystemSettingKey.RESERVED_WORKSPACE_SLUGS_EXTRA,
        [extra],
    )
    await db_session.flush()

    slug, used_random = await reserve_personal_workspace_slug(
        db_session, email=f"{extra}@example.com"
    )
    assert slug.startswith(f"{extra}-")
    assert used_random is True
