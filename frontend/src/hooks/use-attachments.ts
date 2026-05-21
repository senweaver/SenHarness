"use client";

import { useMutation } from "@tanstack/react-query";
import { API_BASE_URL, ApiError } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type AttachmentKind = "image" | "audio" | "video" | "document" | "other";

export interface AttachmentRead {
  id: string;
  workspace_id: string;
  session_id: string | null;
  uploader_identity_id: string | null;
  filename: string;
  mime_type: string;
  size_bytes: number;
  kind: AttachmentKind;
  sha256: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

async function uploadFile(
  file: File,
  sessionId?: string | null,
): Promise<AttachmentRead> {
  const accessToken = useAuthStore.getState().accessToken;
  const workspaceId = useWorkspaceStore.getState().activeWorkspaceId;
  const form = new FormData();
  form.append("file", file);
  if (sessionId) form.append("session_id", sessionId);

  const headers: HeadersInit = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  if (workspaceId) headers["X-Workspace-Id"] = workspaceId;

  const res = await fetch(`${API_BASE_URL}/api/v1/attachments`, {
    method: "POST",
    headers,
    body: form,
    credentials: "include",
  });
  if (!res.ok) {
    let detail = res.statusText;
    let code = "http.error";
    try {
      const env = await res.json();
      detail = env.detail ?? detail;
      code = env.code ?? code;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, code, detail);
  }
  return (await res.json()) as AttachmentRead;
}

export function useUploadAttachment(sessionId?: string | null) {
  return useMutation<AttachmentRead, unknown, File>({
    mutationFn: (file) => uploadFile(file, sessionId),
  });
}

/**
 * Stable authed URL for the raw bytes — used as ``<img src>`` on the client.
 * The URL is protected (Bearer + X-Workspace-Id), so we return a callback
 * that produces a blob URL instead.
 */
export async function fetchAttachmentBlobUrl(
  attachmentId: string,
): Promise<string> {
  const accessToken = useAuthStore.getState().accessToken;
  const workspaceId = useWorkspaceStore.getState().activeWorkspaceId;
  const headers: HeadersInit = {};
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;
  if (workspaceId) headers["X-Workspace-Id"] = workspaceId;
  const res = await fetch(
    `${API_BASE_URL}/api/v1/attachments/${attachmentId}/content`,
    { headers, credentials: "include" },
  );
  if (!res.ok) throw new Error(`http ${res.status}`);
  const blob = await res.blob();
  return URL.createObjectURL(blob);
}
