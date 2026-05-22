import { APIRequestContext, expect, test } from "@playwright/test";
import { randomEmail } from "./helpers";

export interface M0Identity {
    email: string;
    password: string;
    accessToken: string;
    identityId: string;
    workspaceId: string;
    workspaceSlug: string;
}

export async function bootstrapPersonalIdentity(
    request: APIRequestContext,
    baseURL: string,
): Promise<M0Identity> {
    const email = randomEmail();
    const password = "e2e-password-very-long";

    const register = await request.post(`${baseURL}/api/v1/auth/register`, {
        data: { email, name: "M0 E2E", password },
    });
    expect(
        [200, 201].includes(register.status()),
        `register status ${register.status()} body ${await register.text().catch(() => "")}`,
    ).toBe(true);
    const body = (await register.json()) as {
        identity_id?: string;
        id?: string;
        workspace?: { id: string; slug: string } | null;
        auto_login_tokens?: { access_token: string } | null;
    };
    const identityId = body.identity_id ?? body.id ?? "";

    let accessToken = body.auto_login_tokens?.access_token ?? "";
    if (!accessToken) {
        const login = await request.post(`${baseURL}/api/v1/auth/login`, {
            data: { email, password },
        });
        expect(login.ok(), `login body ${await login.text().catch(() => "")}`).toBe(
            true,
        );
        const j = (await login.json()) as { access_token: string };
        accessToken = j.access_token;
    }

    let workspaceId = body.workspace?.id ?? "";
    let workspaceSlug = body.workspace?.slug ?? "";

    if (!workspaceId) {
        const list = await request.get(`${baseURL}/api/v1/workspaces`, {
            headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (list.ok()) {
            const arr = (await list.json()) as Array<{
                id: string;
                slug: string;
            }>;
            const [first] = arr;
            if (first) {
                workspaceId = first.id;
                workspaceSlug = first.slug;
            }
        }
    }

    if (!workspaceId) {
        test.skip(
            true,
            "No personal workspace provisioned after register; M0.9 may be misconfigured.",
        );
    }

    return {
        email,
        password,
        accessToken,
        identityId,
        workspaceId,
        workspaceSlug,
    };
}

export function authHeaders(identity: M0Identity): Record<string, string> {
    return {
        Authorization: `Bearer ${identity.accessToken}`,
        "X-Workspace-Id": identity.workspaceId,
    };
}

export async function loginAdmin(
    request: APIRequestContext,
    baseURL: string,
): Promise<M0Identity> {
    const email =
        process.env.E2E_PLATFORM_ADMIN_EMAIL ??
        "browser-tester@example.com";
    const password =
        process.env.E2E_PLATFORM_ADMIN_PASSWORD ?? "BrowserTest2026!";

    const login = await request.post(`${baseURL}/api/v1/auth/login`, {
        data: { email, password },
    });
    if (!login.ok()) {
        test.skip(
            true,
            `admin login failed: ${login.status()} ${await login.text().catch(() => "")}`,
        );
    }
    const { access_token: accessToken } = (await login.json()) as {
        access_token: string;
    };

    const me = await request.get(`${baseURL}/api/v1/me`, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    const meBody = (await me.json()) as {
        id?: string;
        platform_role?: string;
    };
    if (meBody.platform_role !== "platform_admin") {
        test.skip(
            true,
            `admin login OK but platform_role=${meBody.platform_role ?? "unknown"}`,
        );
    }
    const identityId = meBody.id ?? "";

    const list = await request.get(`${baseURL}/api/v1/workspaces`, {
        headers: { Authorization: `Bearer ${accessToken}` },
    });
    let workspaceId = "";
    let workspaceSlug = "";
    if (list.ok()) {
        const arr = (await list.json()) as Array<{
            id: string;
            slug: string;
        }>;
        const [first] = arr;
        if (first) {
            workspaceId = first.id;
            workspaceSlug = first.slug;
        }
    }

    return {
        email,
        password,
        accessToken,
        identityId,
        workspaceId,
        workspaceSlug,
    };
}

export async function createAgent(
    request: APIRequestContext,
    baseURL: string,
    identity: M0Identity,
    name?: string,
): Promise<{ id: string; name: string }> {
    const finalName = name ?? `M0 Agent ${Date.now()}`;
    const res = await request.post(`${baseURL}/api/v1/agents`, {
        headers: authHeaders(identity),
        data: {
            name: finalName,
            description: "m0 e2e agent",
            persona_md: "Concise assistant.",
            backend_kind: "native",
            visibility: "private",
            autonomy_level: "l2",
            metadata_json: { approvals: false, sandbox: "state" },
        },
    });
    if (!res.ok()) {
        throw new Error(
            `createAgent failed: ${res.status()} ${await res.text()}`,
        );
    }
    const j = (await res.json()) as { id: string; name: string };
    return { id: j.id, name: j.name };
}

export async function createSession(
    request: APIRequestContext,
    baseURL: string,
    identity: M0Identity,
    subjectId: string,
): Promise<{ id: string }> {
    const res = await request.post(`${baseURL}/api/v1/sessions`, {
        headers: authHeaders(identity),
        data: {
            kind: "p2p",
            subject_id: subjectId,
            title: `M0 session ${Date.now()}`,
        },
    });
    if (!res.ok()) {
        throw new Error(
            `createSession failed: ${res.status()} ${await res.text()}`,
        );
    }
    const j = (await res.json()) as { id: string };
    return { id: j.id };
}
