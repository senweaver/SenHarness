"use client";

import * as React from "react";
import { useTranslations } from "next-intl";

import { Card, CardContent } from "@/components/ui/card";
import { ApiError } from "@/lib/api";

import { SkillDiff } from "./SkillDiff";
import {
  useComputeSkillDiff,
  useSkillVersionDiff,
} from "@/hooks/use-skill-diff";

export interface SkillDiffPanelProps {
  /**
   * Direct content path. When both ``oldContent`` and ``newContent``
   * are provided we compute the diff client-side via the
   * ``POST /skills/diff`` mutation (so stats / truncation flag come
   * from the server).
   */
  oldContent?: string;
  newContent?: string;

  /** Versioned path (M1.2 — currently 501). */
  packId?: string;
  versionA?: string;
  versionB?: string;

  fileLabel?: string;
  fromLabel?: string;
  toLabel?: string;
  splitView?: boolean;
  className?: string;
}

export function SkillDiffPanel(props: SkillDiffPanelProps) {
  const {
    oldContent,
    newContent,
    packId,
    versionA,
    versionB,
    fileLabel,
    fromLabel,
    toLabel,
    splitView,
    className,
  } = props;
  const t = useTranslations("skillDiff");

  const inlineMode = oldContent !== undefined && newContent !== undefined;

  const compute = useComputeSkillDiff();
  const versions = useSkillVersionDiff(
    inlineMode ? null : packId,
    inlineMode ? null : versionA,
    inlineMode ? null : versionB,
  );

  React.useEffect(() => {
    if (!inlineMode) return;
    compute.mutate({
      old_content: oldContent ?? "",
      new_content: newContent ?? "",
      file_label: fileLabel,
      from_label: fromLabel,
      to_label: toLabel,
    });
    // We deliberately re-run only when the *content* changes, not on
    // every render (mutate identity is stable enough for this case).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oldContent, newContent, fileLabel, fromLabel, toLabel, inlineMode]);

  if (!inlineMode) {
    if (versions.isError) {
      const err = versions.error;
      const isStub =
        err instanceof ApiError && err.code === "skill.versions_not_implemented";
      return (
        <Card className={className}>
          <CardContent className="space-y-2 py-6 text-center">
            <p className="text-sm font-medium">
              {isStub ? t("versionsNotYetAvailable") : t("loadFailed")}
            </p>
            <p className="text-xs sh-muted">
              {isStub ? t("versionsNotYetAvailableHint") : err?.message}
            </p>
          </CardContent>
        </Card>
      );
    }
    if (versions.isLoading || !versions.data) {
      return (
        <Card className={className}>
          <CardContent className="py-6">
            <div className="h-32 w-full animate-pulse rounded bg-muted/20" />
          </CardContent>
        </Card>
      );
    }
    return (
      <SkillDiff
        oldContent=""
        newContent={versions.data.diff}
        fileLabel={
          fileLabel ?? versions.data.files_changed[0] ?? "SKILL.md"
        }
        fromLabel={fromLabel}
        toLabel={toLabel}
        splitView={splitView}
        added={versions.data.stats.added_lines}
        removed={versions.data.stats.removed_lines}
        truncated={versions.data.truncated}
        className={className}
      />
    );
  }

  if (compute.isPending && !compute.data) {
    return (
      <Card className={className}>
        <CardContent className="py-6">
          <div className="h-32 w-full animate-pulse rounded bg-muted/20" />
        </CardContent>
      </Card>
    );
  }

  return (
    <SkillDiff
      oldContent={oldContent ?? ""}
      newContent={newContent ?? ""}
      fileLabel={fileLabel}
      fromLabel={fromLabel}
      toLabel={toLabel}
      splitView={splitView}
      added={compute.data?.stats.added_lines}
      removed={compute.data?.stats.removed_lines}
      truncated={compute.data?.truncated ?? false}
      className={className}
    />
  );
}
