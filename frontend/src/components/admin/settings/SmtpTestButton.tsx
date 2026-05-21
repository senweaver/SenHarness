"use client";

import { IconMailFast } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useTestSmtp } from "@/hooks/use-platform-settings";

export function SmtpTestButton({
  payload,
}: {
  payload: {
    host?: string;
    port: number;
    username?: string | null;
    password?: string | null;
    from_address?: string | null;
    use_tls: boolean;
  };
}) {
  const t = useTranslations("platformSettings");
  const test = useTestSmtp();
  return (
    <Button
      type="button"
      variant="outline"
      onClick={async () => {
        if (!payload.host || !payload.from_address) {
          toast.error(t("smtpTestMissingFields"));
          return;
        }
        try {
          const result = await test.mutateAsync({
            host: payload.host,
            port: payload.port,
            username: payload.username ?? null,
            password: payload.password ?? null,
            from_address: payload.from_address,
            use_tls: payload.use_tls,
          });
          if (result.ok) {
            toast.success(t("smtpTestOk"));
          } else {
            toast.error(t("smtpTestFailed", { error: result.error ?? "" }));
          }
        } catch (e) {
          toast.error((e as Error)?.message ?? t("smtpTestFailed", { error: "" }));
        }
      }}
      disabled={test.isPending}
    >
      <IconMailFast className="size-4" />
      {t("smtpTestButton")}
    </Button>
  );
}
