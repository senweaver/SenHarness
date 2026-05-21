"use client";

import { useEffect, useRef, useState } from "react";
import {
  IconBrandGithub,
  IconClipboard,
  IconFileZip,
  IconLink,
  IconLoader2,
  IconPuzzle,
} from "@tabler/icons-react";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  useImportSkillFromBundle,
  useImportSkillFromUrl,
  useUploadSkill,
} from "@/hooks/use-skills";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

type TabKey = "url" | "github" | "zip" | "paste";

interface SkillImportHubDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const TABS: Array<{ key: TabKey; icon: React.ReactNode; labelKey: string }> = [
  { key: "url", icon: <IconLink className="size-3.5" />, labelKey: "tabUrl" },
  { key: "github", icon: <IconBrandGithub className="size-3.5" />, labelKey: "tabGithub" },
  { key: "zip", icon: <IconFileZip className="size-3.5" />, labelKey: "tabZip" },
  { key: "paste", icon: <IconClipboard className="size-3.5" />, labelKey: "tabPaste" },
];

const MAX_BUNDLE_BYTES = 5 * 1024 * 1024;

/**
 * `SkillImportHubDialog` — single dialog with four tabs for importing
 * an Anthropic-style Agent Skill (a folder containing SKILL.md plus
 * optional reference docs and scripts).
 *
 *   - URL / GitHub: fetched server-side via `/skills/import-url`
 *     (avoids browser CORS, supports github.com/blob and tree URLs)
 *   - ZIP: uploaded as multipart/form-data to `/skills/import-bundle`,
 *     extracted server-side so the entire folder structure is
 *     preserved (references / scripts included)
 *   - Paste: existing single-file SKILL.md upload
 */
export function SkillImportHubDialog({
  open,
  onOpenChange,
}: SkillImportHubDialogProps) {
  const t = useTranslations("skillImport");
  const tCommon = useTranslations("common");

  const importUrl = useImportSkillFromUrl();
  const importBundle = useImportSkillFromBundle();
  const uploadPaste = useUploadSkill();

  const [tab, setTab] = useState<TabKey>("url");
  const [url, setUrl] = useState("");
  const [githubUrl, setGithubUrl] = useState("");
  const [paste, setPaste] = useState("");
  const [slug, setSlug] = useState("");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setUrl("");
      setGithubUrl("");
      setPaste("");
      setSlug("");
      setZipFile(null);
      setTab("url");
    }
  }, [open]);

  const isPending =
    importUrl.isPending || importBundle.isPending || uploadPaste.isPending;

  const formatError = (err: unknown): string => {
    if (err instanceof ApiError) return err.code || err.message;
    return (err as Error)?.message ?? "unknown";
  };

  const onSubmit = async () => {
    try {
      if (tab === "paste") {
        const content = paste.trim();
        if (!content) {
          toast.error(t("missingContent"));
          return;
        }
        const fallback =
          slug.trim() ||
          (content.match(/^name:\s*(.+)$/m)?.[1] ?? "").trim() ||
          `imported-${Date.now().toString(36)}`;
        await uploadPaste.mutateAsync({
          slug: fallback.toLowerCase().replace(/[^a-z0-9_-]+/g, "-").slice(0, 63),
          content,
        });
      } else if (tab === "url" || tab === "github") {
        const value = (tab === "url" ? url : githubUrl).trim();
        if (!value) {
          toast.error(t("missingUrl"));
          return;
        }
        await importUrl.mutateAsync({
          url: value,
          slug: slug.trim() || null,
        });
      } else if (tab === "zip") {
        if (!zipFile) {
          toast.error(t("missingZip"));
          return;
        }
        if (zipFile.size > MAX_BUNDLE_BYTES) {
          toast.error(t("importFailed", { error: "bundle_too_large" }));
          return;
        }
        await importBundle.mutateAsync({
          file: zipFile,
          slug: slug.trim() || null,
        });
      }
      toast.success(t("imported"));
      onOpenChange(false);
    } catch (err) {
      toast.error(t("importFailed", { error: formatError(err) }));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[720px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconPuzzle className="size-4" />
            {t("title")}
          </DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="mb-3 flex gap-1 rounded-md border bg-black/5 p-1 dark:bg-white/5">
          {TABS.map((tabDef) => {
            const isActive = tabDef.key === tab;
            return (
              <button
                key={tabDef.key}
                type="button"
                onClick={() => setTab(tabDef.key)}
                className={cn(
                  "flex flex-1 items-center justify-center gap-1.5 rounded px-3 py-1.5 text-[12px] font-medium transition-colors",
                  isActive
                    ? "bg-white text-[rgb(var(--color-fg))] shadow-sm dark:bg-slate-800"
                    : "sh-muted hover:text-[rgb(var(--color-fg))]",
                )}
              >
                {tabDef.icon}
                {t(tabDef.labelKey)}
              </button>
            );
          })}
        </div>

        <div className="space-y-3">
          {tab === "url" && (
            <div className="space-y-1.5">
              <Label>{t("urlLabel")}</Label>
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={t("urlPlaceholder")}
                className="font-mono text-[12px]"
              />
              <p className="text-[11px] sh-muted">{t("urlHint")}</p>
            </div>
          )}

          {tab === "github" && (
            <div className="space-y-1.5">
              <Label>{t("githubLabel")}</Label>
              <Input
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                placeholder={t("githubPlaceholder")}
                className="font-mono text-[12px]"
              />
              <p className="text-[11px] sh-muted">{t("githubHint")}</p>
            </div>
          )}

          {tab === "zip" && (
            <div className="space-y-2">
              <Label>{t("zipLabel")}</Label>
              <button
                type="button"
                onClick={() => fileRef.current?.click()}
                className="flex w-full flex-col items-center gap-2 rounded-md border border-dashed p-6 text-[12px] transition-colors hover:bg-black/5 dark:hover:bg-white/5"
              >
                <IconFileZip className="size-6 sh-muted" />
                <span>{t("zipDrop")}</span>
                {zipFile && (
                  <span className="font-mono text-[11px] text-emerald-600">
                    ✓ {zipFile.name} ({Math.ceil(zipFile.size / 1024)} KB)
                  </span>
                )}
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".zip,application/zip"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0] ?? null;
                  setZipFile(file);
                }}
              />
              <p className="text-[11px] sh-muted">{t("zipHint")}</p>
            </div>
          )}

          {tab === "paste" && (
            <div className="space-y-1.5">
              <Label>{t("pasteLabel")}</Label>
              <Textarea
                value={paste}
                onChange={(e) => setPaste(e.target.value)}
                placeholder={t("pastePlaceholder")}
                className="min-h-[260px] font-mono text-[12px]"
              />
            </div>
          )}

          <div className="space-y-1.5">
            <Label>{t("slugLabel")}</Label>
            <Input
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
              placeholder="my-skill (auto if blank)"
              className="font-mono text-[12px]"
            />
            <p className="text-[11px] sh-muted">{t("slugHint")}</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={() => void onSubmit()}
            disabled={isPending}
          >
            {isPending && <IconLoader2 className="size-4 animate-spin" />}
            {t("import")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
