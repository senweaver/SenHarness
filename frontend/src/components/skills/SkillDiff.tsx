"use client";

import * as React from "react";
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useTranslations } from "next-intl";
import { Copy, FileText } from "lucide-react";

import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const ReactDiffViewer = dynamic(
  () => import("react-diff-viewer-continued"),
  {
    ssr: false,
    loading: () => (
      <div className="h-32 w-full animate-pulse rounded bg-muted/20" />
    ),
  },
);

export interface SkillDiffProps {
  oldContent: string;
  newContent: string;
  fileLabel?: string;
  fromLabel?: string;
  toLabel?: string;
  splitView?: boolean;
  showLineNumbers?: boolean;
  truncated?: boolean;
  added?: number;
  removed?: number;
  className?: string;
}

export function SkillDiff({
  oldContent,
  newContent,
  fileLabel = "SKILL.md",
  fromLabel,
  toLabel,
  splitView = true,
  showLineNumbers = true,
  truncated = false,
  added,
  removed,
  className,
}: SkillDiffProps) {
  const t = useTranslations("skillDiff");
  const { resolvedTheme } = useTheme();
  const isDark = resolvedTheme === "dark";

  const isUnchanged = oldContent === newContent;

  const onCopy = React.useCallback(async () => {
    const payload = newContent || oldContent;
    try {
      await navigator.clipboard.writeText(payload);
      toast.success(t("copiedToast"));
    } catch {
      // clipboard unavailable in restricted contexts (file://, iframes)
    }
  }, [newContent, oldContent, t]);

  const computedAdded = added ?? countSign(oldContent, newContent, "+");
  const computedRemoved = removed ?? countSign(oldContent, newContent, "-");

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardHeader className="flex-row items-center justify-between gap-2 border-b">
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 sh-muted" />
          <CardTitle className="text-sm">{fileLabel}</CardTitle>
          {!isUnchanged ? (
            <span className="ml-2 flex items-center gap-1 text-xs">
              <Badge variant="success" aria-label={t("addedLines")}>
                +{computedAdded}
              </Badge>
              <Badge variant="danger" aria-label={t("removedLines")}>
                −{computedRemoved}
              </Badge>
            </span>
          ) : null}
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onCopy}
          aria-label={t("copyDiff")}
          className="h-7 px-2 text-xs"
        >
          <Copy className="mr-1 h-3 w-3" />
          {t("copyDiff")}
        </Button>
      </CardHeader>

      <CardContent className="p-0">
        {isUnchanged ? (
          <div className="p-6 text-center text-sm sh-muted">
            {t("noChanges")}
          </div>
        ) : (
          <div className="text-xs">
            <ReactDiffViewer
              oldValue={oldContent}
              newValue={newContent}
              splitView={splitView}
              hideLineNumbers={!showLineNumbers}
              useDarkTheme={isDark}
              leftTitle={fromLabel}
              rightTitle={toLabel}
            />
          </div>
        )}
        {truncated ? (
          <div className="border-t bg-amber-500/10 px-4 py-2 text-xs text-amber-700 dark:text-amber-400">
            {t("truncatedNotice")}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function countSign(oldText: string, newText: string, sign: "+" | "-"): number {
  if (oldText === newText) return 0;
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");
  if (sign === "+") {
    const oldSet = new Set(oldLines);
    return newLines.filter((line) => !oldSet.has(line)).length;
  }
  const newSet = new Set(newLines);
  return oldLines.filter((line) => !newSet.has(line)).length;
}
