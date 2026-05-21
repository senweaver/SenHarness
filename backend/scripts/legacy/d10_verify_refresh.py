"""D10 — verify access-token refresh preserves workspace_id claim."""
from __future__ import annotations

import asyncio

import httpx

from app.main import app


async def main() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        # 1) Login
        r = await c.post(
            "/api/v1/auth/login",
            json={"email": "demo@senharness.app", "password": "senharness"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        access = data["access_token"]
        print(f"login: ws-in-access = {_get_claim(access, 'ws')!r}")
        refresh_cookie = None
        for k, v in c.cookies.items():
            if k == "sh_refresh":
                refresh_cookie = v
                break
        assert refresh_cookie is not None, "no refresh cookie set"

        # 2) Hit /me with the access token — should succeed, workspace_id present
        r = await c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert r.status_code == 200, r.text
        me = r.json()
        print(f"me.current_workspace_id = {me.get('current_workspace_id')}  role = {me.get('current_role')}")

        # 3) Refresh
        r = await c.post("/api/v1/auth/refresh")
        assert r.status_code == 200, r.text
        new_access = r.json()["access_token"]
        new_ws = _get_claim(new_access, "ws")
        print(f"refresh: ws-in-new-access = {new_ws!r}")
        assert new_ws, "BUG: refresh dropped the ws claim"

        # 4) Hit /me with the refreshed token — should still return workspace
        r = await c.get(
            "/api/v1/me", headers={"Authorization": f"Bearer {new_access}"}
        )
        assert r.status_code == 200, r.text
        me2 = r.json()
        print(f"me-after-refresh.current_workspace_id = {me2.get('current_workspace_id')}")

        # 5) Sanity: a workspace-scoped endpoint works with the new token
        ws_id = me2.get("current_workspace_id")
        r = await c.get(
            "/api/v1/agents?limit=1",
            headers={
                "Authorization": f"Bearer {new_access}",
                "X-Workspace-Id": ws_id or "",
            },
        )
        print(f"agents list after refresh: status={r.status_code}")
        assert r.status_code == 200, r.text

        print("\n[PASS] refresh preserves workspace claim end-to-end")


def _get_claim(token: str, key: str) -> object:
    import base64
    import json

    _, payload_b64, _ = token.split(".")
    # JWT base64 is URL-safe, add padding
    pad = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    return payload.get(key)


if __name__ == "__main__":
    asyncio.run(main())
