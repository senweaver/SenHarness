"use client";

import { IconKeyFilled } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { useTestOAuth } from "@/hooks/use-platform-settings";

export function OAuthTestButton({ provider }: { provider: string }) {
  const t = useTranslations("platformSettings");
  const test = useTestOAuth();
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={async () => {
        try {
          const result = await test.mutateAsync({ provider });
          if (result.ok) {
            toast.success(t("oauthTestOk", { provider }));
          } else {
            toast.error(
              t("oauthTestFailed", {
                provider,
                error: result.error ?? "",
              }),
            );
          }
        } catch (e) {
          toast.error((e as Error)?.message ?? "");
        }
      }}
      disabled={test.isPending}
    >
      <IconKeyFilled className="size-3" />
      {t("oauthTestButton", { provider })}
    </Button>
  );
}
