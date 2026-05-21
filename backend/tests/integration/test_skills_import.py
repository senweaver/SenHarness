"""Skill bundle / URL import end-to-end.

Covers Anthropic Agent Skills directory shape: a SKILL.md plus
sibling reference docs and scripts. Bundled ZIPs are extracted
server-side so the workspace-skills directory mirrors the structure
the runtime expects.
"""

from __future__ import annotations

import io
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.asyncio


async def _bootstrap_admin_workspace(async_client) -> tuple[dict, str]:
    """Register identity, create workspace, return auth headers + workspace id."""
    email = f"skills-{uuid.uuid4().hex[:8]}@example.com"
    password = "skills-test-password-long"
    r = await async_client.post(
        "/api/v1/auth/register",
        json={"email": email, "name": "Skills Tester", "password": password},
    )
    assert r.status_code == 201, r.text
    r = await async_client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    slug = f"skill-ws-{uuid.uuid4().hex[:6]}"
    r = await async_client.post(
        "/api/v1/workspaces",
        headers=headers,
        json={"name": "Skill Test", "slug": slug},
    )
    assert r.status_code in (200, 201)
    ws_id = r.json()["id"]
    headers["X-Workspace-Id"] = ws_id
    return headers, ws_id


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP from a {posix_path: bytes} mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


SKILL_MD = b"""---
name: bundle-demo
description: Imported via ZIP bundle.
license: MIT
---

# Bundle demo

This skill ships with a reference doc and a helper script.
"""


async def test_import_bundle_persists_folder_structure(async_client):
    headers, _ws_id = await _bootstrap_admin_workspace(async_client)

    payload = _make_zip(
        {
            "SKILL.md": SKILL_MD,
            "references/style-guide.md": b"# Style guide\n\n- be terse\n",
            "scripts/run.sh": b"#!/usr/bin/env bash\necho hi\n",
        }
    )

    r = await async_client.post(
        "/api/v1/skills/import-bundle",
        headers=headers,
        files={"file": ("bundle-demo.zip", payload, "application/zip")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["slug"] == "bundle-demo"
    assert body["source"] == "workspace"

    # Listing exposes the new skill.
    r = await async_client.get("/api/v1/skills", headers=headers)
    assert r.status_code == 200
    names = {row["slug"] for row in r.json()}
    assert "bundle-demo" in names

    # Detail returns the SKILL.md content (with front-matter).
    r = await async_client.get(
        "/api/v1/skills/workspace/bundle-demo", headers=headers
    )
    assert r.status_code == 200
    assert "Bundle demo" in r.json()["content"]


async def test_import_bundle_strips_common_top_dir(async_client):
    """`zip -r foo.zip foo/` produces a single-prefix archive — we
    should peel that prefix off so the skill ends up flat."""
    headers, _ = await _bootstrap_admin_workspace(async_client)
    payload = _make_zip(
        {
            "outer/SKILL.md": SKILL_MD,
            "outer/references/x.md": b"# x\n",
        }
    )
    r = await async_client.post(
        "/api/v1/skills/import-bundle",
        headers=headers,
        files={"file": ("outer.zip", payload, "application/zip")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["slug"] == "bundle-demo"


async def test_import_bundle_rejects_path_traversal(async_client):
    headers, _ = await _bootstrap_admin_workspace(async_client)
    payload = _make_zip(
        {
            "SKILL.md": SKILL_MD,
            "../etc/evil.txt": b"naughty",
        }
    )
    r = await async_client.post(
        "/api/v1/skills/import-bundle",
        headers=headers,
        files={"file": ("evil.zip", payload, "application/zip")},
    )
    assert r.status_code == 400
    assert "traversal" in r.json()["detail"]


async def test_import_bundle_requires_skill_md(async_client):
    headers, _ = await _bootstrap_admin_workspace(async_client)
    payload = _make_zip({"README.md": b"# nope\n"})
    r = await async_client.post(
        "/api/v1/skills/import-bundle",
        headers=headers,
        files={"file": ("noskill.zip", payload, "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "missing_skill_md"


async def test_import_bundle_rejects_bad_zip(async_client):
    headers, _ = await _bootstrap_admin_workspace(async_client)
    r = await async_client.post(
        "/api/v1/skills/import-bundle",
        headers=headers,
        files={"file": ("not-a-zip.zip", b"not really a zip", "application/zip")},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_zip"
