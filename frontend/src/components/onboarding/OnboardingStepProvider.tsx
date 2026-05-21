"use client";

import { useMemo, useState } from "react";
import {
  IconArrowRight,
  IconCheck,
  IconLoader2,
  IconPlugConnected,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useCreateProvider,
  useProviderCatalog,
  useProviders,
  useTestProvider,
  type ProviderCatalogEntry,
  type ProviderRead,
} from "@/hooks/use-providers";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { cn } from "@/lib/utils";

interface OnboardingStepProviderProps {
  onNext: () => void;
}

export function OnboardingStepProvider({ onNext }: OnboardingStepProviderProps) {
  const t = useTranslations("onboarding.provider");
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
    return catalog.slice(0, 8);
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
      const created = await create.mutateAsync({
        kind: pickedKind,
        name: selectedEntry.display_name,
        api_key: apiKey.trim(),
        base_url: baseUrl.trim() || null,
      });
      setDraft({ providerId: created.id });
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
          name: selectedEntry.display_name,
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

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {recommendedEntries.map((entry) => {
          const isActive = entry.kind === pickedKind;
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
              <span className="text-[13px] font-semibold">
                {entry.display_name}
              </span>
              <span className="line-clamp-2 text-[11px] sh-muted">
                {entry.description}
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
