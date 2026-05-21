"use client";

import { use, useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Link } from "@/lib/navigation";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { DangerousChangeDialog } from "@/components/admin/settings/DangerousChangeDialog";
import { OAuthTestButton } from "@/components/admin/settings/OAuthTestButton";
import { SectionForm } from "@/components/admin/settings/SectionForm";
import { SmtpTestButton } from "@/components/admin/settings/SmtpTestButton";
import {
  usePlatformSection,
  usePlatformSectionSchema,
  usePlatformSections,
  useResetPlatformSection,
  useUpdatePlatformSection,
} from "@/hooks/use-platform-settings";

export default function AdminPlatformSettingsPage({
  params,
}: {
  params: Promise<{ section: string; locale: string }>;
}) {
  const { section } = use(params);
  const t = useTranslations("platformSettings");
  const list = usePlatformSections();
  const detail = usePlatformSection(section);
  const schema = usePlatformSectionSchema(section);
  const update = useUpdatePlatformSection();
  const reset = useResetPlatformSection();

  const [pendingValue, setPendingValue] = useState<Record<
    string,
    unknown
  > | null>(null);
  const [confirmFields, setConfirmFields] = useState<string[]>([]);

  const sections = list.data?.sections ?? [];

  const onSave = async (
    value: Record<string, unknown>,
    confirmed: boolean,
  ) => {
    try {
      await update.mutateAsync({
        section,
        value,
        confirmed_dangerous: confirmed,
      });
      toast.success(t("savedToast"));
      setPendingValue(null);
      setConfirmFields([]);
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.code === "platform_settings.dangerous_change_requires_confirmation"
      ) {
        const fields = (e.extras?.fields as string[]) ?? [];
        setPendingValue(value);
        setConfirmFields(fields);
        return;
      }
      const code =
        e instanceof ApiError ? e.code : (e as Error)?.message ?? "";
      toast.error(t("saveFailed", { code }));
    }
  };

  const onReset = async () => {
    try {
      await reset.mutateAsync({ section });
      toast.success(t("resetToast"));
    } catch (e) {
      toast.error((e as Error)?.message ?? "");
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader title={t("title")} description={t("description")} />
      <div className="grid gap-4 lg:grid-cols-[220px_1fr]">
        <SectionSidebar sections={sections} active={section} />
        <Card>
          <CardHeader>
            <CardTitle>{t(`sections.${section}.label`)}</CardTitle>
            <CardDescription>
              {t(`sections.${section}.description`)}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-5">
            {(!detail.data || !schema.data) && <Skeleton className="h-48" />}
            {detail.data && schema.data && (
              <>
                <SectionForm
                  section={detail.data}
                  schema={schema.data}
                  onSave={onSave}
                  onReset={onReset}
                  saving={update.isPending}
                  resetting={reset.isPending}
                />
                {section === "email.smtp" && (
                  <SmtpTestPanel value={detail.data.value} />
                )}
                {section === "auth.oauth" && (
                  <OAuthTestPanel value={detail.data.value} />
                )}
                {detail.data.last_modified_at && (
                  <p className="text-[11px] sh-muted">
                    {t("lastModifiedAt", {
                      time: new Date(
                        detail.data.last_modified_at,
                      ).toLocaleString(),
                    })}
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <DangerousChangeDialog
        open={confirmFields.length > 0}
        onOpenChange={(open) => {
          if (!open) {
            setConfirmFields([]);
            setPendingValue(null);
          }
        }}
        fields={confirmFields}
        loading={update.isPending}
        onConfirm={() => {
          if (pendingValue) {
            onSave(pendingValue, true);
          }
        }}
      />
    </div>
  );
}

function SectionSidebar({
  sections,
  active,
}: {
  sections: { section: string }[];
  active: string;
}) {
  const t = useTranslations("platformSettings");
  return (
    <nav className="rounded-md border p-1 text-sm">
      {sections.length === 0 && (
        <div className="p-3 text-xs sh-muted">{t("loading")}</div>
      )}
      {sections.map((s) => {
        const isActive = s.section === active;
        return (
          <Link
            key={s.section}
            href={`/admin/settings/${s.section}`}
            className={cn(
              "block rounded px-3 py-1.5 text-[13px] transition-colors",
              isActive
                ? "bg-black/5 font-medium dark:bg-white/10"
                : "hover:bg-black/5 dark:hover:bg-white/5",
            )}
          >
            {safeT(t, `sections.${s.section}.label`, s.section)}
          </Link>
        );
      })}
    </nav>
  );
}

function safeT(
  t: ReturnType<typeof useTranslations>,
  key: string,
  fallback: string,
): string {
  try {
    const value = t(key);
    return value || fallback;
  } catch {
    return fallback;
  }
}

function SmtpTestPanel({ value }: { value: Record<string, unknown> }) {
  const t = useTranslations("platformSettings");
  const payload = useMemo(
    () => ({
      host: typeof value.host === "string" ? value.host : undefined,
      port: typeof value.port === "number" ? value.port : 587,
      username:
        typeof value.username === "string"
          ? value.username
          : value.username === null
            ? null
            : null,
      password: null,
      from_address:
        typeof value.from_address === "string"
          ? value.from_address
          : undefined,
      use_tls: Boolean(value.use_tls ?? true),
    }),
    [value],
  );
  return (
    <div className="rounded-md border bg-black/[.02] p-3 dark:bg-white/[.02]">
      <p className="mb-2 text-[12px] sh-muted">{t("smtpTestHint")}</p>
      <SmtpTestButton payload={payload} />
    </div>
  );
}

function OAuthTestPanel({ value }: { value: Record<string, unknown> }) {
  const t = useTranslations("platformSettings");
  useEffect(() => undefined, []);
  const providers = Array.isArray(value.providers)
    ? (value.providers as Array<{ name: string; enabled?: boolean }>)
    : [];
  if (providers.length === 0) return null;
  return (
    <div className="rounded-md border bg-black/[.02] p-3 dark:bg-white/[.02]">
      <p className="mb-2 text-[12px] sh-muted">{t("oauthTestHint")}</p>
      <div className="flex flex-wrap gap-2">
        {providers.map((p) => (
          <OAuthTestButton key={p.name} provider={p.name} />
        ))}
      </div>
    </div>
  );
}
