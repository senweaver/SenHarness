"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export type DocSourceKind = "text" | "url" | "file";
export type DocStatus = "pending" | "ingesting" | "ready" | "failed";

export interface KnowledgeCollectionCard {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  config_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  doc_count: number;
  chunk_count: number;
}

export interface KnowledgeDocRead {
  id: string;
  collection_id: string;
  title: string;
  source_kind: DocSourceKind;
  source_uri: string | null;
  status: DocStatus;
  error: string | null;
  chunk_count: number;
  metadata_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface KnowledgeChunkHit {
  id: string;
  doc_id: string;
  doc_title: string | null;
  ord: number;
  text: string;
  score: number;
}

export function useCollections() {
  const token = useAuthStore((s) => s.accessToken);
  const ws = useWorkspaceStore((s) => s.activeWorkspaceId);
  return useQuery<KnowledgeCollectionCard[]>({
    queryKey: ["knowledge", "collections", ws],
    queryFn: () =>
      api.get<KnowledgeCollectionCard[]>("/api/v1/knowledge/collections"),
    enabled: Boolean(token && ws),
  });
}

export function useCollection(id: string | null | undefined) {
  return useQuery<KnowledgeCollectionCard>({
    queryKey: ["knowledge", "collection", id],
    queryFn: async () => {
      const all = await api.get<KnowledgeCollectionCard[]>(
        "/api/v1/knowledge/collections",
      );
      const row = all.find((c) => c.id === id);
      if (!row) throw new Error("collection_not_found");
      return row;
    },
    enabled: Boolean(id),
  });
}

export function useDocs(collectionId: string | null | undefined) {
  return useQuery<KnowledgeDocRead[]>({
    queryKey: ["knowledge", "docs", collectionId],
    queryFn: () =>
      api.get<KnowledgeDocRead[]>(
        `/api/v1/knowledge/collections/${collectionId}/docs`,
      ),
    enabled: Boolean(collectionId),
  });
}

export function useCreateCollection() {
  const qc = useQueryClient();
  return useMutation<
    KnowledgeCollectionCard,
    unknown,
    { name: string; description?: string | null }
  >({
    mutationFn: (input) =>
      api.post<KnowledgeCollectionCard>(
        "/api/v1/knowledge/collections",
        input,
      ),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["knowledge", "collections"] }),
  });
}

export function useDeleteCollection() {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (id) => api.delete(`/api/v1/knowledge/collections/${id}`),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["knowledge", "collections"] }),
  });
}

export function useIngestDoc(collectionId: string) {
  const qc = useQueryClient();
  return useMutation<
    KnowledgeDocRead,
    unknown,
    {
      title: string;
      source_kind: DocSourceKind;
      source_uri?: string | null;
      raw_text?: string | null;
    }
  >({
    mutationFn: (input) =>
      api.post<KnowledgeDocRead>(
        `/api/v1/knowledge/collections/${collectionId}/docs`,
        input,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge", "docs", collectionId] });
      qc.invalidateQueries({ queryKey: ["knowledge", "collections"] });
    },
  });
}

export interface IngestAttachmentError {
  /** Machine-readable error code surfaced by the backend. Possible values:
   * `unsupported_kind` (audio/video/image), `unsupported_mime`,
   * `file_too_large`, `pdf_lib_missing`, `pdf_parse_failed`, `pdf_empty`,
   * `attachment.blob_missing`. */
  code: string;
  message: string;
}

/**
 * `useIngestAttachment` — one-click import of an existing chat attachment
 * into a knowledge collection. The endpoint extracts text server-side
 * (UTF-8 for textual mime, pypdf for PDFs) and runs the usual chunk-embed
 * pipeline, so on success the collection's doc list grows by one.
 *
 * Errors come back as HTTP 415 with a ``{code, message}`` body; callers
 * should surface ``code`` so the toast can tell the user *why* a given file
 * can't be ingested.
 */
export function useIngestAttachment() {
  const qc = useQueryClient();
  return useMutation<
    KnowledgeDocRead,
    { response?: { status?: number; data?: { detail?: IngestAttachmentError } } },
    { collectionId: string; attachmentId: string; title?: string | null }
  >({
    mutationFn: ({ collectionId, attachmentId, title }) =>
      api.post<KnowledgeDocRead>(
        `/api/v1/knowledge/collections/${collectionId}/ingest_attachment`,
        { attachment_id: attachmentId, title: title ?? null },
      ),
    onSuccess: (_d, { collectionId }) => {
      qc.invalidateQueries({ queryKey: ["knowledge", "docs", collectionId] });
      qc.invalidateQueries({ queryKey: ["knowledge", "collections"] });
    },
  });
}

export function useDeleteDoc(collectionId: string) {
  const qc = useQueryClient();
  return useMutation<void, unknown, string>({
    mutationFn: (docId) =>
      api.delete(`/api/v1/knowledge/collections/${collectionId}/docs/${docId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge", "docs", collectionId] });
      qc.invalidateQueries({ queryKey: ["knowledge", "collections"] });
    },
  });
}

export function useSearchCollection() {
  return useMutation<
    KnowledgeChunkHit[],
    unknown,
    { collectionId: string; query: string; top_k?: number }
  >({
    mutationFn: ({ collectionId, query, top_k }) =>
      api.post<KnowledgeChunkHit[]>(
        `/api/v1/knowledge/collections/${collectionId}/search`,
        { query, top_k: top_k ?? 5 },
      ),
  });
}
