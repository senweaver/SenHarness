"use client";

/**
 * Workspace MEMORY.md editor.
 *
 * Admin-only (the server enforces the same — this UI-level gate just
 * spares workspace members from hitting a 403). MEMORY.md gets injected
 * into every agent's system prompt, so the cap is strict (~2.2 KB).
 */

import { useEffect } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconBrain } from "@tabler/icons-react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { MarkdownProfileEditor } from "@/components/memory-profiles/MarkdownProfileEditor";
import {
  MEMORY_CAPS,
  useWorkspaceMemory,
  usePutWorkspaceMemory,
} from "@/hooks/use-memory-profiles";
import { useMe } from "@/hooks/use-me";

const ADMIN_ROLES = new Set(["owner", "admin"]);

export default function WorkspaceMemoryPage() {
  const t = useTranslations("settings.workspaceMemory");
  const router = useRouter();
  const { data: me, isLoading: meLoading } = useMe();
  const { data: profile, isLoading } = useWorkspaceMemory();
  const put = usePutWorkspaceMemory();

  useEffect(() => {
    if (!meLoading && me && !ADMIN_ROLES.has(me.current_role ?? "")) {
      router.replace("/settings");
    }
  }, [meLoading, me, router]);

  if (meLoading || !me) return <Skeleton className="h-24" />;
  if (!ADMIN_ROLES.has(me.current_role ?? "")) return null;

  const cap = MEMORY_CAPS.workspace_memory;
  const content = profile?.content_md ?? "";

  const onSave = async (next: string) => {
    try {
      await put.mutateAsync({ content_md: next });
      toast.success(t("saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    }
  };

  return (
    <div data-testid="workspace-memory-page">
      <PageHeader title={t("title")} description={t("description", { cap })} />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <IconBrain className="size-4" />
            MEMORY.md
          </CardTitle>
          <CardDescription>{t("adminOnly")}</CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-48" />
          ) : (
            <MarkdownProfileEditor
              ns="settings.workspaceMemory"
              initialContent={content}
              maxChars={cap}
              onSave={onSave}
              saving={put.isPending}
              testIdPrefix="workspace-memory"
            />
          )}
        </CardContent>
      </Card>
    </div>
  );
}
