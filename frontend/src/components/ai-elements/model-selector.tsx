"use client";

/**
 * Adapted from Vercel AI SDK AI Elements.
 *
 * Per-agent model picker for the chat composer. Reads from
 * ``GET /api/v1/agents/{id}/models`` (workspace-enabled providers plus
 * per-provider ``provider_models.enabled`` rows) and persists the
 * selection to the caller's profile via
 * ``PUT /api/v1/me/preferences/models``.
 *
 * This component is presentation only — it doesn't call ``useChat`` /
 * ``setModel`` hooks. The parent (`ChatInput`) owns the selected id and
 * forwards it to the WS transport so it lands as ``RunRequest.model_override``
 * on the next ``user_message`` frame.
 */

import { IconBolt, IconChevronDown, IconCpu, IconStar } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  useAgentModels,
  type AgentModelOption,
} from "@/hooks/use-agent-models";
import { cn } from "@/lib/utils";

export interface ModelSelectorProps {
  agentId: string | null | undefined;
  /** Currently selected ``provider:model`` id (or null = use agent default). */
  value: string | null;
  /** Called when the user picks a row. ``null`` = "reset to agent default". */
  onChange: (modelId: string | null) => void;
  className?: string;
}

/** Family → tiny lucide-style icon. Kept inline to avoid yet another file. */
function formatModelOptionLabel(
  opt: AgentModelOption,
  agentDefaultSuffix?: string,
): string {
  const providerLabel = opt.provider_display_name || opt.provider;
  const modelLabel = opt.name || opt.model;
  const base = `${providerLabel} / ${modelLabel}`;
  return agentDefaultSuffix ? `${base} (${agentDefaultSuffix})` : base;
}

function FamilyBadge({ family }: { family: string }) {
  if (family === "frontier" || family === "reasoning") {
    return <IconStar className="size-3 text-amber-500" />;
  }
  if (family === "fast" || family === "local") {
    return <IconBolt className="size-3 text-emerald-500" />;
  }
  return <IconCpu className="size-3 text-muted-foreground" />;
}

export function ModelSelector({
  agentId,
  value,
  onChange,
  className,
}: ModelSelectorProps) {
  const t = useTranslations("chat.compose");
  const query = useAgentModels(agentId ?? null);

  // Snap the displayed selection to a real catalog row. If the saved value
  // points at a model the catalog no longer carries, fall back to the
  // backend-resolved default so the trigger isn't blank.
  const activeOption: AgentModelOption | null = useMemo(() => {
    const opts = query.data?.options ?? [];
    if (opts.length === 0) return null;
    if (value) {
      const hit = opts.find((o) => o.id === value);
      if (hit) return hit;
    }
    return opts.find((o) => o.is_default) ?? opts.find((o) => o.recommended) ?? opts[0]!;
  }, [query.data, value]);

  // Hide the picker entirely when the backend has no provider configured —
  // the user can't pick a model that has no key behind it. The "configure
  // provider" hint lives in the workspace pane, not the composer.
  if (query.isLoading) {
    return null;
  }
  if (!query.data || !query.data.provider || query.data.options.length === 0) {
    return null;
  }

  const grouped = groupByProvider(query.data.options);
  const triggerLabel = activeOption
    ? formatModelOptionLabel(
        activeOption,
        value ? undefined : t("modelAgentDefault"),
      )
    : t("modelLabel");

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className={cn("h-7 gap-1 px-2 text-xs", className)}
          data-testid="chat-model"
          data-model={activeOption?.id ?? ""}
        >
          {activeOption ? <FamilyBadge family={activeOption.family} /> : null}
          <span className="max-w-[180px] truncate">{triggerLabel}</span>
          <IconChevronDown className="size-3 sh-muted" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        <DropdownMenuLabel>{t("modelLabel")}</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {grouped.map(({ provider, displayName, items }) => (
          <DropdownMenuGroup key={provider}>
            <DropdownMenuLabel className="text-[10px] uppercase tracking-wide text-muted-foreground">
              {displayName}
            </DropdownMenuLabel>
            {items.map((opt) => (
              <DropdownMenuItem
                key={opt.id}
                onSelect={() => onChange(opt.id)}
                data-active={(activeOption?.id ?? "") === opt.id}
                className="flex flex-col items-start gap-0.5"
              >
                <div className="flex w-full items-center gap-2">
                  <FamilyBadge family={opt.family} />
                  <span className="text-xs font-medium">{opt.name}</span>
                  {opt.recommended ? (
                    <span className="ml-auto rounded-sm bg-emerald-50 px-1 py-px text-[10px] font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
                      {t("modelRecommended")}
                    </span>
                  ) : opt.is_default ? (
                    <span className="ml-auto rounded-sm bg-muted px-1 py-px text-[10px] font-medium text-muted-foreground">
                      {t("modelDefault")}
                    </span>
                  ) : null}
                </div>
                {opt.description ? (
                  <span className="text-[10px] sh-muted">{opt.description}</span>
                ) : null}
              </DropdownMenuItem>
            ))}
          </DropdownMenuGroup>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => onChange(null)}
          className="text-xs text-muted-foreground"
        >
          {t("modelReset")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function groupByProvider(
  options: AgentModelOption[],
): { provider: string; displayName: string; items: AgentModelOption[] }[] {
  const buckets = new Map<
    string,
    { displayName: string; items: AgentModelOption[] }
  >();
  for (const opt of options) {
    const bucket = buckets.get(opt.provider);
    if (bucket) {
      bucket.items.push(opt);
    } else {
      buckets.set(opt.provider, {
        displayName: opt.provider_display_name || opt.provider,
        items: [opt],
      });
    }
  }
  return Array.from(buckets.entries()).map(([provider, { displayName, items }]) => ({
    provider,
    displayName,
    items,
  }));
}
