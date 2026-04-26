"use client";

/**
 * MarkdownProfileEditor — editable markdown body + live char-count gauge.
 *
 * Shared between the workspace MEMORY.md editor and the per-identity
 * USER.md editor. Soft-enforces the server cap: it lets you over-type
 * (so you can see how much to trim) but surfaces the gauge in red past
 * the cap.
 */

import { IconLoader2 } from "@tabler/icons-react";
import { useState, useEffect } from "react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export interface MarkdownProfileEditorProps {
  /** Which localisation namespace this editor uses — `workspaceMemory` or `soul`. */
  ns: "settings.workspaceMemory" | "settings.soul";
  /** The existing markdown body; empty string means "not set yet". */
  initialContent: string;
  placeholder?: string;
  maxChars: number;
  /** Submit label override — defaults to common.save. */
  submitLabel?: string;
  onSave: (content: string) => Promise<void>;
  saving?: boolean;
  readOnly?: boolean;
  /** Optional test id prefix for Playwright — default is `profile`. */
  testIdPrefix?: string;
}

export function MarkdownProfileEditor({
  ns,
  initialContent,
  placeholder,
  maxChars,
  submitLabel,
  onSave,
  saving = false,
  readOnly = false,
  testIdPrefix = "profile",
}: MarkdownProfileEditorProps) {
  const t = useTranslations(ns);
  const tCommon = useTranslations("common");
  const [content, setContent] = useState(initialContent);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    setContent(initialContent);
    setDirty(false);
  }, [initialContent]);

  const count = content.length;
  const pct = Math.min(100, Math.round((count / maxChars) * 100));
  const over = count > maxChars;

  return (
    <div className="space-y-2" data-testid={`${testIdPrefix}-editor`}>
      <Textarea
        value={content}
        onChange={(e) => {
          setContent(e.target.value);
          setDirty(e.target.value !== initialContent);
        }}
        placeholder={placeholder ?? t("placeholder")}
        className={cn(
          "min-h-[240px] font-mono text-[13px] leading-relaxed",
          over && "border-destructive",
        )}
        readOnly={readOnly}
        data-testid={`${testIdPrefix}-textarea`}
      />

      <div className="flex items-center gap-3">
        <div className="flex-1">
          <div className="h-1.5 w-full rounded-full bg-black/5 dark:bg-white/10">
            <div
              className={cn(
                "h-1.5 rounded-full transition-all",
                over ? "bg-red-500" : pct > 80 ? "bg-amber-500" : "bg-green-500",
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          <p
            className={cn(
              "mt-0.5 text-[10px] tabular-nums",
              over ? "text-destructive" : "sh-muted",
            )}
          >
            {t("saveHint", { n: count, cap: maxChars })}
          </p>
        </div>

        {!readOnly && (
          <Button
            size="sm"
            disabled={saving || !dirty}
            onClick={async () => {
              await onSave(content);
              setDirty(false);
            }}
            data-testid={`${testIdPrefix}-save`}
          >
            {saving && <IconLoader2 className="size-4 animate-spin" />}
            {submitLabel ?? tCommon("save")}
          </Button>
        )}
      </div>
    </div>
  );
}

/** Utility Label wrapper used by profile pages to wrap the editor. */
export function ProfileFieldWrapper({
  label,
  description,
  children,
}: {
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label className="text-sm font-medium">{label}</Label>
      {description && <p className="text-[11px] sh-muted">{description}</p>}
      {children}
    </div>
  );
}
