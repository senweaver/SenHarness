"use client";

import { useMemo, useState } from "react";
import {
  IconArrowRight,
  IconCheck,
  IconLoader2,
  IconPlugConnected,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useCreateProvider,
  useProviderCatalog,
  useProviders,
  useTestProvider,
  type DiscoverResponse,
  type ProviderCatalogEntry,
  type ProviderModelRead,
  type ProviderRead,
} from "@/hooks/use-providers";
import { ProviderAvatar } from "@/components/providers/ProviderAvatar";
import { labelOf, descOf } from "@/components/providers/_localize";
import { api } from "@/lib/api";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { cn } from "@/lib/utils";

interface OnboardingStepProviderProps {
  onNext: () => void;
}

export function OnboardingStepProvider({ onNext }: OnboardingStepProviderProps) {
  const t = useTranslations("onboarding.provider");
  const locale = useLocale();
  const setDraft = useOnboardingStore((s) => s.setDraft);

  const { data: catalog } = useProviderCatalog();
  const { data: providers } = useProviders();
  const create = useCreateProvider();

  const [pickedKind, setPickedKind] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [testProviderId, setTestProviderId] = useState<string | null>(null);
  const test = useTestProvider(testProviderId ?? "");

  const recommendedEntries = useMemo<ProviderCatalogEntry[]>(() => {
    if (!catalog) return [];
    return catalog.filter((entry) => entry.kind !== "custom");
  }, [catalog]);

  const selectedEntry = useMemo(
    () => catalog?.find((entry) => entry.kind === pickedKind) ?? null,
    [catalog, pickedKind],
  );

  const onSelect = (kind: string) => {
    setPickedKind(kind);
    const entry = catalog?.find((c) => c.kind === kind);
    if (entry?.default_base_url) {
      setBaseUrl(entry.default_base_url);
    } else {
      setBaseUrl("");
    }
    setApiKey("");
    setTestProviderId(null);
  };

  const autoDiscoverAndApply = async (providerId: string) => {
    // First-run shortcut: run discover then apply the recommended set so
    // the agent step finds usable models without a second manual visit
    // to /settings/workspace/providers. Static fallback is fine — the
    // catalog ships a sensible default for every supported provider.
    let discovered: DiscoverResponse;
    try {
      discovered = await api.post<DiscoverResponse>(
        `/api/v1/providers/${providerId}/discover`,
        undefined,
      );
    } catch {
      return;
    }
    const candidates = discovered.discovered;
    if (!candidates.length) return;
    const recommended = candidates.filter((m) => m.recommended);
    const picks = (recommended.length > 0 ? recommended : candidates.slice(0, 3))
      .filter((m) => !m.in_db)
      .map((m) => m.model);
    if (!picks.length) return;
    try {
      await api.post<ProviderModelRead[]>(
        `/api/v1/providers/${providerId}/discover/apply`,
        { model_ids: picks, replace: false },
      );
    } catch {
      // Apply failure isn't fatal — user can still pick models manually
      // later. Onboarding intentionally continues so the chat step
      // doesn't strand them.
    }
  };

  const saveAndContinue = async () => {
    if (!pickedKind || !selectedEntry) {
      toast.error(t("pickRequired"));
      return;
    }
    if (!apiKey.trim()) {
      toast.error(t("apiKeyRequired"));
      return;
    }
    try {
      let providerId = testProviderId;
      if (!providerId) {
        const created = await create.mutateAsync({
          kind: pickedKind,
          name: labelOf(selectedEntry, locale),
          api_key: apiKey.trim(),
          base_url: baseUrl.trim() || null,
        });
        providerId = created.id;
      }
      setDraft({ providerId });
      await autoDiscoverAndApply(providerId);
      onNext();
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  const runTest = async () => {
    let providerId = testProviderId;
    if (!providerId) {
      if (!pickedKind || !selectedEntry || !apiKey.trim()) {
        toast.error(t("apiKeyRequired"));
        return;
      }
      try {
        const created = await create.mutateAsync({
          kind: pickedKind,
          name: labelOf(selectedEntry, locale),
          api_key: apiKey.trim(),
          base_url: baseUrl.trim() || null,
        });
        providerId = created.id;
        setTestProviderId(created.id);
        setDraft({ providerId: created.id });
      } catch {
        toast.error(t("saveFailed"));
        return;
      }
    }
    try {
      const result = await test.mutateAsync({});
      if (result.ok) {
        toast.success(t("testOk"));
      } else {
        toast.error(result.error ?? t("testFailed"));
      }
    } catch {
      toast.error(t("testFailed"));
    }
  };

  const existing = (providers ?? []).filter((p) => p.has_key);

  return (
    <div className="flex flex-col gap-5 px-6 py-6">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">{t("title")}</h2>
        <p className="text-sm sh-muted">{t("subtitle")}</p>
      </div>

      {existing.length > 0 && (
        <div className="rounded-md border border-dashed p-3">
          <p className="text-[12px] font-medium">{t("existingTitle")}</p>
          <ul className="mt-1 space-y-0.5 text-[12px] sh-muted">
            {existing.slice(0, 3).map((p: ProviderRead) => (
              <li key={p.id} className="flex items-center gap-1.5">
                <IconCheck className="size-3 text-[rgb(var(--color-primary))]" />
                <span>{p.name}</span>
                <span className="text-[10px]">({p.kind})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="grid max-h-[280px] grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3 md:grid-cols-4">
        {recommendedEntries.map((entry) => {
          const isActive = entry.kind === pickedKind;
          const name = labelOf(entry, locale);
          return (
            <button
              key={entry.kind}
              type="button"
              onClick={() => onSelect(entry.kind)}
              className={cn(
                "flex flex-col items-start gap-1 rounded-md border p-3 text-left transition-colors",
                isActive
                  ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary)/0.05)]"
                  : "sh-card hover:bg-black/5 dark:hover:bg-white/5",
              )}
            >
              <span className="flex items-center gap-2">
                <ProviderAvatar
                  displayName={name}
                  family={entry.family}
                  size="sm"
                />
                <span className="text-[13px] font-semibold">{name}</span>
              </span>
              <span className="line-clamp-2 text-[11px] sh-muted">
                {descOf(entry, locale)}
              </span>
            </button>
          );
        })}
      </div>

      {selectedEntry && (
        <div className="space-y-3 rounded-md border p-3">
          <div className="space-y-1.5">
            <Label htmlFor="onboarding-provider-key">{t("apiKeyLabel")}</Label>
            <Input
              id="onboarding-provider-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={t("apiKeyPlaceholder")}
            />
            <p className="text-[11px] sh-muted">{t("apiKeyHint")}</p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="onboarding-provider-base-url">
              {t("baseUrlLabel")}
            </Label>
            <Input
              id="onboarding-provider-base-url"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={selectedEntry.default_base_url ?? ""}
            />
          </div>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <button
          type="button"
          onClick={onNext}
          className="text-[11px] sh-muted hover:underline"
        >
          {t("skip")}
        </button>
        <div className="flex gap-2">
          {selectedEntry && (
            <Button
              variant="outline"
              onClick={runTest}
              disabled={create.isPending || test.isPending || !apiKey.trim()}
            >
              {test.isPending && <IconLoader2 className="size-4 animate-spin" />}
              <IconPlugConnected className="size-4" />
              {t("test")}
            </Button>
          )}
          <Button
            onClick={saveAndContinue}
            disabled={
              !selectedEntry || !apiKey.trim() || create.isPending
            }
          >
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {t("save")}
            <IconArrowRight className="size-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
