"use client";

import { useState } from "react";
import { IconPlus, IconX } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  type SenderAllowlistMode,
  type SenderAllowlistRules,
} from "@/hooks/use-channels";
import { cn } from "@/lib/utils";

interface SenderAllowlistPanelProps {
  rules: SenderAllowlistRules;
  onChange: (next: SenderAllowlistRules) => void;
  className?: string;
}

const MODES: SenderAllowlistMode[] = ["allow_all", "allow_listed", "deny_listed"];

export function SenderAllowlistPanel({
  rules,
  onChange,
  className,
}: SenderAllowlistPanelProps) {
  const t = useTranslations("channelSecurity");
  const mode: SenderAllowlistMode = rules.mode ?? "allow_all";
  const list = mode === "allow_listed" ? rules.allow ?? [] : rules.deny ?? [];

  const [draft, setDraft] = useState("");

  const setMode = (m: SenderAllowlistMode) => {
    if (m === "allow_all") {
      onChange({ mode: "allow_all" });
    } else if (m === "allow_listed") {
      onChange({ mode: "allow_listed", allow: rules.allow ?? rules.deny ?? [] });
    } else {
      onChange({ mode: "deny_listed", deny: rules.deny ?? rules.allow ?? [] });
    }
  };

  const addEntry = () => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    if (list.includes(trimmed)) return;
    const next = [...list, trimmed];
    if (mode === "allow_listed") {
      onChange({ mode, allow: next });
    } else if (mode === "deny_listed") {
      onChange({ mode, deny: next });
    }
    setDraft("");
  };

  const removeEntry = (entry: string) => {
    const next = list.filter((e) => e !== entry);
    if (mode === "allow_listed") {
      onChange({ mode, allow: next });
    } else if (mode === "deny_listed") {
      onChange({ mode, deny: next });
    }
  };

  return (
    <div
      className={cn(
        "space-y-3 rounded-md border bg-[rgb(var(--color-card))] p-3",
        className,
      )}
    >
      <div>
        <Label className="text-[12px] font-medium">
          {t("senderAllowlistTitle")}
        </Label>
        <p className="mt-0.5 text-[11px] sh-muted">{t("senderAllowlistHint")}</p>
      </div>

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        {MODES.map((m) => (
          <button
            key={m}
            type="button"
            onClick={() => setMode(m)}
            className={cn(
              "rounded-md border px-2 py-1.5 text-left text-xs transition",
              "hover:border-[rgb(var(--color-primary))]",
              mode === m &&
                "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))]/5",
            )}
            aria-pressed={mode === m}
          >
            <div className="font-medium">{t(`mode${capitalize(m)}` as never)}</div>
            <div className="text-[11px] sh-muted">
              {t(`mode${capitalize(m)}Hint` as never)}
            </div>
          </button>
        ))}
      </div>

      {mode !== "allow_all" && (
        <div className="space-y-2">
          <Label className="text-[12px]">{t("senderListLabel")}</Label>
          <div className="flex gap-2">
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder={t("senderListPlaceholder")}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addEntry();
                }
              }}
            />
            <Button type="button" size="sm" onClick={addEntry}>
              <IconPlus className="size-3.5" />
            </Button>
          </div>
          {list.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {list.map((entry) => (
                <Badge
                  key={entry}
                  variant="outline"
                  className="gap-1 pl-2 pr-1"
                >
                  <span className="font-mono text-[11px]">{entry}</span>
                  <button
                    type="button"
                    onClick={() => removeEntry(entry)}
                    className="rounded p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
                    aria-label={t("senderListRemove")}
                  >
                    <IconX className="size-3" />
                  </button>
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-[11px] sh-muted">{t("senderListEmpty")}</p>
          )}
        </div>
      )}
    </div>
  );
}

function capitalize(value: string): string {
  return value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}
