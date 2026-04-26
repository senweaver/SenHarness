"use client";

import { use, useState } from "react";
import { Link } from "@/lib/navigation";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { IconLoader2 } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useAuthStore } from "@/stores/auth-store";
import { toast } from "sonner";

export default function InviteAcceptPage({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const { code } = use(params);
  const t = useTranslations("settings.inviteAccept");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const token = useAuthStore((s) => s.accessToken);
  const [busy, setBusy] = useState(false);

  const accept = async () => {
    if (!token) {
      toast.info(t("loginRequired"));
      router.push("/login");
      return;
    }
    setBusy(true);
    try {
      await api.post("/api/v1/workspaces/invitations/accept", { code });
      toast.success(t("joined"));
      router.push("/");
    } catch (e: unknown) {
      toast.error((e as { message?: string }).message ?? "accept failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid="invite-accept-page"
      className="flex min-h-screen items-center justify-center p-4"
    >
      <Card className="w-full max-w-md">
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>
            {t("codeLabel")}：<span className="font-mono">{code.slice(0, 12)}…</span>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {!token && (
            <div className="rounded-md border p-3 text-xs sh-muted">
              {t("loginNote")}{" "}
              <Link className="underline" href="/login">{t("loginLink")}</Link>{" "}
              {t.rich("loginCta", {
                register: (chunks) => (
                  <Link className="underline" href="/register">
                    {chunks}
                  </Link>
                ),
              })}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => router.push("/")}>
              {tCommon("cancel")}
            </Button>
            <Button
              data-testid="invite-accept-submit"
              onClick={accept}
              disabled={busy}
            >
              {busy && <IconLoader2 className="size-4 animate-spin" />}
              {t("accept")}
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
