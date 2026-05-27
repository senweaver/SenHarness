/**
 * Workspace switching helper — single source of truth for the
 * `POST /workspaces/{id}/switch` ritual.
 *
 * Call this whenever a UI surface needs to change the active workspace
 * (sidebar switcher, deep-link landing pages, chat preflight when the
 * session's `workspace_id` doesn't match the current token's `ws` claim).
 *
 * Side effects on success:
 *  - new access token persisted via ``useAuthStore.setAccess``
 *  - active workspace id persisted via ``useWorkspaceStore.setActive``
 *
 * Returns ``true`` on success, ``false`` if the request failed.
 */

import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

/**
 * Normalize a user-typed name into a valid workspace slug
 * (`/^[a-z0-9][a-z0-9-]{1,63}$/`). Empty result is the caller's problem.
 */
export function slugifyWorkspaceName(name: string): string {
  return name
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 60);
}

export async function switchActiveWorkspace(workspaceId: string): Promise<boolean> {
  // Note: we deliberately do NOT short-circuit when
  // ``activeWorkspaceId === workspaceId``. The Zustand store can drift
  // from the actual JWT ``ws`` claim (e.g. after navigating an old tab
  // whose token was minted under a different workspace), and what the
  // backend cares about is the token claim. Always re-mint the token
  // and let backend validation be the source of truth.

  try {
    const data = await api.post<{ access_token: string; expires_at?: string }>(
      `/api/v1/workspaces/${workspaceId}/switch`,
    );
    // Token TTL is 30 min; mirror what WorkspaceSwitcher.tsx does in the
    // absence of a server-provided expires_at.
    const expiresAt =
      data.expires_at ?? new Date(Date.now() + 30 * 60_000).toISOString();
    useAuthStore.getState().setAccess(data.access_token, expiresAt);
    useWorkspaceStore.getState().setActive(workspaceId);
    return true;
  } catch {
    return false;
  }
}
