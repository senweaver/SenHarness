"use client";

import { useMemo, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconArrowRight,
  IconLoader2,
  IconPlus,
  IconTrash,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDeleteMemory, useMemories } from "@/hooks/use-memories";
import { AddMemoryDialog } from "@/components/agents/dialogs/AddMemoryDialog";

export function MemoryTab({ agentId }: { agentId: string }) {
  const t = useTranslations("agentDetail.memory");
  const tMem = useTranslations("settings.memory");
  const [open, setOpen] = useState(false);
  const remove = useDeleteMemory();

  const { data, isLoading } = useMemories({ scope: "assistant" });
  const items = useMemo(
    () => (data ?? []).filter((m) => m.scope_id === agentId),
    [data, agentId],
  );

  const onDelete = async (id: string) => {
    if (!confirm(tMem("confirmDelete"))) return;
    try {
      await remove.mutateAsync(id);
      toast.success(tMem("deleted"));
    } catch {
      toast.error(tMem("deleteFailed"));
    }
  };

  return (
    <div className="space-y-4">
      <header className="flex items-center justify-between">
        <h2 className="text-base font-semibold">{t("title")}</h2>
        <Button size="sm" onClick={() => setOpen(true)}>
          <IconPlus className="size-4" />
          {t("addCta")}
        </Button>
      </header>

      {isLoading ? (
        <div className="flex items-center gap-2 rounded-md border p-4 text-[13px] sh-muted">
          <IconLoader2 className="size-4 animate-spin" />…
        </div>
      ) : items.length === 0 ? (
        <p className="rounded-md border border-dashed p-8 text-center text-[13px] sh-muted">
          {t("empty")}
        </p>
      ) : (
        <ul className="rounded-md border sh-card divide-y">
          {items.map((m) => (
            <li
              key={m.id}
              className="flex items-start gap-3 px-4 py-3 text-sm"
            >
              <div className="min-w-0 flex-1">
                <div className="mb-1 flex items-center gap-1.5">
                  <Badge variant="outline">{tMem(`kind.${m.kind}`)}</Badge>
                  {m.key && (
                    <span className="font-mono text-[11px] sh-muted">
                      {m.key}
                    </span>
                  )}
                </div>
                <p className="line-clamp-3 text-[13px]">{m.content}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => void onDelete(m.id)}
                aria-label={tMem("delete")}
              >
                <IconTrash className="size-4 text-red-500" />
              </Button>
            </li>
          ))}
        </ul>
      )}

      <Link
        href="/settings/profile?tab=soul"
        className="inline-flex items-center gap-1 text-[12px] font-medium text-[rgb(var(--color-primary))] hover:underline"
      >
        {t("soulLink")}
        <IconArrowRight className="size-3.5" />
      </Link>

      <AddMemoryDialog open={open} onOpenChange={setOpen} agentId={agentId} />
    </div>
  );
}
