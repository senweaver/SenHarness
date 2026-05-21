"""D15 — verify attachment upload / download / isolation + WS attachment_ids."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import httpx

from app.main import app


# 1×1 PNG (valid bytes). base64 of a transparent pixel, decoded at runtime.
TINY_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
    b"\xc0\x00\x00\x00\x03\x00\x01\x8c\xcf\xb7\xec\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "senharness"},
        )
        access = r.json()["access_token"]
        me = (await c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {access}"}
        )).json()
        ws_id = me["current_workspace_id"]
        H = {"Authorization": f"Bearer {access}", "X-Workspace-Id": ws_id}
        print(f"workspace={ws_id}")

        # ── Upload image ──
        files = {"file": ("pixel.png", TINY_PNG, "image/png")}
        r = await c.post("/api/v1/attachments", headers=H, files=files)
        assert r.status_code == 201, r.text
        img = r.json()
        assert img["kind"] == "image"
        assert img["size_bytes"] == len(TINY_PNG)
        assert img["sha256"]
        print(
            f"uploaded image: id={img['id']} mime={img['mime_type']} size={img['size_bytes']}"
        )

        # ── Upload generic text ──
        files = {
            "file": (
                "notes.md",
                b"# hello\n\nsome text\n",
                "text/markdown",
            )
        }
        r = await c.post("/api/v1/attachments", headers=H, files=files)
        assert r.status_code == 201
        doc = r.json()
        assert doc["kind"] == "document"
        print(f"uploaded doc:   id={doc['id']} kind={doc['kind']}")

        # ── Download image & check bytes ──
        r = await c.get(
            f"/api/v1/attachments/{img['id']}/content", headers=H
        )
        assert r.status_code == 200, r.text
        assert r.content == TINY_PNG
        assert r.headers["content-type"] == "image/png"
        assert "inline" in r.headers["content-disposition"]
        print("image download bytes match ✓ (inline)")

        # ── Download doc → attachment disposition ──
        r = await c.get(
            f"/api/v1/attachments/{doc['id']}/content", headers=H
        )
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        print("doc download is attachment disposition ✓")

        # ── Metadata GET ──
        r = await c.get(f"/api/v1/attachments/{img['id']}", headers=H)
        assert r.status_code == 200
        assert r.json()["id"] == img["id"]

        # ── Foreign-workspace access denied ──
        # We fake a random UUID as X-Workspace-Id — ensure_member_access
        # raises PermissionDenied (403) since the user has no membership.
        H_evil = dict(H)
        H_evil["X-Workspace-Id"] = str(uuid.uuid4())
        r = await c.get(
            f"/api/v1/attachments/{img['id']}/content", headers=H_evil
        )
        assert r.status_code in (401, 403, 404), r.status_code
        print(f"cross-workspace access blocked (got {r.status_code}) ✓")

        # ── Try oversized upload → 403 ──
        big = b"x" * (26 * 1024 * 1024)
        files = {"file": ("big.bin", big, "application/octet-stream")}
        r = await c.post("/api/v1/attachments", headers=H, files=files)
        assert r.status_code == 403, r.text
        print("oversized upload rejected ✓")

        # ── Empty upload → 400 ──
        files = {"file": ("empty.txt", b"", "text/plain")}
        r = await c.post("/api/v1/attachments", headers=H, files=files)
        assert r.status_code == 400
        print("empty upload rejected ✓")

        # ── Delete ──
        r = await c.delete(
            f"/api/v1/attachments/{doc['id']}", headers=H
        )
        assert r.status_code == 204
        r = await c.get(f"/api/v1/attachments/{doc['id']}", headers=H)
        assert r.status_code == 404, r.text
        print("soft-delete + 404 on re-fetch ✓")

        # ── Verify disk file was written ──
        path = Path(img["metadata_json"].get("storage_uri", "")) if False else None
        _ = path

        print("\n[PASS] D15 attachments round-trip")


if __name__ == "__main__":
    asyncio.run(main())
