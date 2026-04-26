"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconLoader2 } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { useActiveWorkspace, useUpdateWorkspace } from "@/hooks/use-workspace";
import { useMe } from "@/hooks/use-me";

const AGENT_TERM_CHOICES = ["default", "digital_employee", "agent", "partner", "secretary"] as const;

export default function BrandingSettingsPage() {
  const t = useTranslations("settings.branding");
  const tSettings = useTranslations("settings");
  const tTerm = useTranslations("agentTerm");
  const { data: ws } = useActiveWorkspace();
  const { data: me } = useMe();
  const update = useUpdateWorkspace();

  const [agentTerm, setAgentTerm] = useState<string>("agent");
  const [welcomeH1, setWelcomeH1] = useState<string>("");
  const [primaryColor, setPrimaryColor] = useState<string>("#2E5BFF");
  const [logoUrl, setLogoUrl] = useState<string>("");

  useEffect(() => {
    if (!ws) return;
    const b = (ws.branding_json ?? {}) as Record<string, string>;
    const rawTerm = b.agent_term ?? "agent";
    setAgentTerm(
      (AGENT_TERM_CHOICES as readonly string[]).includes(rawTerm) ? rawTerm : "agent",
    );
    setWelcomeH1(b.welcome_h1 ?? "");
    setPrimaryColor(b.primary_color ?? "#2E5BFF");
    setLogoUrl(b.logo_url ?? "");
  }, [ws?.id, ws]);

  const submit = async () => {
    try {
      await update.mutateAsync({
        branding_json: {
          agent_term: agentTerm,
          welcome_h1: welcomeH1 || null,
          primary_color: primaryColor,
          logo_url: logoUrl || null,
        },
      });
      toast.success(tSettings("saved"));
    } catch {
      toast.error(tSettings("saveFailed"));
    }
  };

  const previewWelcome = (welcomeH1 || t("welcomeFallback", { name: "{name}" })).replace(
    "{name}",
    me?.name ?? "—",
  );

  return (
    <div>
      <PageHeader title={t("title")} description={t("description")} />

      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <Field id="agent-term" label={t("agentTerm")} description={t("agentTermDesc")}>
            <Select value={agentTerm} onValueChange={setAgentTerm}>
              <SelectTrigger className="max-w-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {AGENT_TERM_CHOICES.map((k) => (
                  <SelectItem key={k} value={k}>
                    {tTerm(k as "default")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Field id="welcome" label={t("welcomeH1")} description={t("welcomeH1Desc")}>
            <Input
              id="welcome"
              value={welcomeH1}
              onChange={(e) => setWelcomeH1(e.target.value)}
              placeholder={t("welcomeFallback", { name: "{name}" })}
              className="max-w-2xl"
            />
          </Field>

          <Field id="primary" label={t("primaryColor")} description={t("primaryColorDesc")}>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={primaryColor}
                onChange={(e) => setPrimaryColor(e.target.value)}
                className="h-9 w-14 cursor-pointer rounded-md border"
              />
              <Input
                value={primaryColor}
                onChange={(e) => setPrimaryColor(e.target.value)}
                className="max-w-[120px]"
              />
            </div>
          </Field>

          <Field id="logo" label={t("logoUrl")} description={t("logoUrlDesc")}>
            <Input
              id="logo"
              value={logoUrl}
              onChange={(e) => setLogoUrl(e.target.value)}
              placeholder="https://…/logo.png"
              className="max-w-2xl"
            />
          </Field>
        </CardContent>
      </Card>

      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{t("preview")}</CardTitle>
        </CardHeader>
        <CardContent>
          <div
            className="rounded-lg border p-6 text-center"
            style={{ backgroundColor: "rgb(var(--color-card))" }}
          >
            <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-md text-sm font-bold text-white"
              style={{ backgroundColor: primaryColor }}>
              S
            </div>
            <div className="text-base font-semibold">{previewWelcome}</div>
            <div className="mt-2 text-[11px] sh-muted">
              {t("previewAgentLabel")} <strong>{tTerm(agentTerm as "default")}</strong>
            </div>
          </div>
        </CardContent>
      </Card>

      <div className="mt-4 flex justify-end">
        <Button onClick={submit} disabled={update.isPending}>
          {update.isPending && <IconLoader2 className="size-4 animate-spin" />}
          {t("save")}
        </Button>
      </div>
    </div>
  );
}

function Field({
  id,
  label,
  description,
  children,
}: {
  id: string;
  label: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {description && <p className="text-[11px] sh-muted">{description}</p>}
    </div>
  );
}
