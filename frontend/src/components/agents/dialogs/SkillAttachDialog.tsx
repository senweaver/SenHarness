"use client";

import { useMemo, useState } from "react";
import { IconCheck, IconLoader2, IconPlus } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import { useSkills, type SkillRead } from "@/hooks/use-skills";
import { Link } from "@/lib/navigation";
import { cn } from "@/lib/utils";
import type { AgentRead } from "@/types/api";

interface SkillAttachDialogProps {
  agent: AgentRead;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function readAttachedSlugs(agent: AgentRead): string[] {
  const meta = (agent.metadata_json ?? {}) as { skills?: unknown };
  if (!Array.isArray(meta.skills)) return [];
  return meta.skills
    .map((s) => (typeof s === "string" ? s : null))
    .filter((s): s is string => Boolean(s));
}

export function SkillAttachDialog({
  agent,
  open,
  onOpenChange,
}: SkillAttachDialogProps) {
  const t = useTranslations("agentDetail.abilities.attach");
  const { data: skills, isLoading } = useSkills();
  const update = useUpdateAgent(agent.id);

  const attached = useMemo(() => readAttachedSlugs(agent), [agent]);
  const attachedSet = useMemo(() => new Set(attached), [attached]);

  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [query, setQuery] = useState("");

  const candidates = useMemo<SkillRead[]>(() => {
    const all = skills ?? [];
    const workspace = all.filter(
      (s) => s.source === "workspace" && !attachedSet.has(s.slug),
    );
    const needle = query.trim().toLowerCase();
    if (!needle) return workspace;
    return workspace.filter(
      (s) =>
        s.name.toLowerCase().includes(needle) ||
        s.slug.toLowerCase().includes(needle) ||
        s.description.toLowerCase().includes(needle),
    );
  }, [skills, attachedSet, query]);

  const toggle = (slug: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  };

  const onConfirm = async () => {
    if (selected.size === 0) {
      onOpenChange(false);
      return;
    }
    const merged = [...attached, ...Array.from(selected)];
    const meta = { ...(agent.metadata_json ?? {}), skills: merged };
    try {
      await update.mutateAsync({ metadata_json: meta });
      toast.success(t("attached", { count: selected.size }));
      setSelected(new Set());
      onOpenChange(false);
    } catch (err) {
      toast.error(t("attachFailed", { error: (err as Error).message }));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            className="text-sm"
          />

          <div className="max-h-72 overflow-y-auto rounded-md border">
            {isLoading ? (
              <div className="flex items-center justify-center p-6 text-sm sh-muted">
                <IconLoader2 className="mr-2 size-4 animate-spin" />
                {t("loading")}
              </div>
            ) : candidates.length === 0 ? (
              <div className="space-y-1 p-6 text-center text-sm sh-muted">
                <p>{t("empty")}</p>
                <p className="text-xs">
                  {t.rich("emptyHint", {
                    link: (chunks) => (
                      <Link
                        href="/skills"
                        className="text-primary underline-offset-2 hover:underline"
                      >
                        {chunks}
                      </Link>
                    ),
                  })}
                </p>
              </div>
            ) : (
              <ul className="divide-y">
                {candidates.map((s) => {
                  const checked = selected.has(s.slug);
                  return (
                    <li key={`${s.source}/${s.slug}`}>
                      <button
                        type="button"
                        onClick={() => toggle(s.slug)}
                        className={cn(
                          "flex w-full items-start gap-3 px-3 py-2 text-left transition hover:bg-muted",
                          checked && "bg-primary/5",
                        )}
                      >
                        <span
                          className={cn(
                            "mt-0.5 flex size-4 flex-shrink-0 items-center justify-center rounded border",
                            checked
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-muted-foreground/40",
                          )}
                        >
                          {checked ? <IconCheck className="size-3" /> : null}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-sm font-medium">
                            {s.name}
                          </span>
                          <span className="block truncate text-xs sh-muted">
                            /{s.slug}
                          </span>
                          {s.description ? (
                            <span className="mt-0.5 block truncate text-xs sh-muted">
                              {s.description}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={update.isPending}
          >
            {t("cancel")}
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={selected.size === 0 || update.isPending}
          >
            {update.isPending ? (
              <IconLoader2 className="mr-1 size-4 animate-spin" />
            ) : (
              <IconPlus className="mr-1 size-4" />
            )}
            {t("attach", { count: selected.size })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
