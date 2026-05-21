"use client";

/**
 * SoulDimsCard — 12-dimension SOUL user-model view.
 *
 * Maps against `backend/app/db/models/memory_profile.py::SOUL_DIMENSIONS`.
 * Each dimension is a string (markdown fragment) or absent. Read-only —
 * updates always flow through the pending-proposal queue.
 */

import { useTranslations } from "next-intl";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

// Must match SOUL_DIMENSIONS in memory_profile.py.
export const SOUL_DIMENSIONS: readonly string[] = [
  "communication_style",
  "domain_expertise",
  "tone_and_register",
  "goals_current",
  "constraints",
  "preferences_tools",
  "preferences_language",
  "cadence",
  "identity_signals",
  "workflow",
  "avoid_list",
  "history_summary",
] as const;

interface SoulDimsCardProps {
  dims: Record<string, unknown>;
}

function formatDimKey(k: string): string {
  return k
    .split("_")
    .map((p) => p.charAt(0).toUpperCase() + p.slice(1))
    .join(" ");
}

export function SoulDimsCard({ dims }: SoulDimsCardProps) {
  const t = useTranslations("settings.soul");

  const entries = SOUL_DIMENSIONS.map((k) => {
    const raw = dims[k];
    return {
      key: k,
      label: formatDimKey(k),
      value: typeof raw === "string" ? raw : raw == null ? "" : JSON.stringify(raw),
    };
  });

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base">{t("soulDims")}</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="divide-y">
          {entries.map((e) => (
            <div
              key={e.key}
              className="grid grid-cols-[160px_1fr] gap-3 py-2 text-sm"
            >
              <dt className="sh-muted">{e.label}</dt>
              <dd className="break-words whitespace-pre-wrap">
                {e.value || (
                  <span className="sh-muted">{t("soulDimEmpty")}</span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      </CardContent>
    </Card>
  );
}
