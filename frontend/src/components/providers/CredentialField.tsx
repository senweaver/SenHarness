"use client";

import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { IconKey } from "@tabler/icons-react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { CredentialType } from "@/hooks/use-providers";

interface OAuthHelp {
  href: string;
  label: string;
}

const OAUTH_HELPERS: Record<string, OAuthHelp> = {
  anthropic: {
    href: "https://www.anthropic.com/claude-code",
    label: "Claude Pro / Coding Plan / Claude Code",
  },
};

export function CredentialField({
  kind,
  type,
  hasKey,
  value,
  onChange,
  customHeaders,
  onCustomHeadersChange,
  trailingAction,
}: {
  kind: string;
  type: CredentialType;
  hasKey: boolean;
  value: string;
  onChange: (value: string) => void;
  customHeaders?: string;
  onCustomHeadersChange?: (value: string) => void;
  trailingAction?: ReactNode;
}) {
  const t = useTranslations("settings.providers.credentials");

  if (type === "oauth_token") {
    const helper = OAUTH_HELPERS[kind];
    return (
      <div className="space-y-2">
        <Label className="flex items-center gap-1.5 text-sm">
          <IconKey className="size-3.5" />
          {t("oauthLabel")}
        </Label>
        <p className="text-xs text-muted-foreground">
          {t("oauthHint")}
          {helper ? (
            <>
              {" "}
              <a
                href={helper.href}
                target="_blank"
                rel="noopener"
                className="underline-offset-2 hover:underline text-foreground"
              >
                {helper.label}
              </a>
            </>
          ) : null}
        </p>
        <MaskedKeyInput
          value={value}
          onChange={onChange}
          placeholder={
            hasKey ? t("oauthPlaceholderSaved") : t("oauthPlaceholderEmpty")
          }
          trailingAction={trailingAction}
        />
      </div>
    );
  }

  if (type === "custom_headers") {
    return (
      <div className="space-y-2">
        <Label className="text-sm">{t("customHeadersLabel")}</Label>
        <p className="text-xs text-muted-foreground">
          {t("customHeadersHint")}
        </p>
        <Textarea
          rows={5}
          value={customHeaders ?? ""}
          onChange={(e) => onCustomHeadersChange?.(e.target.value)}
          placeholder={'{\n  "X-API-Version": "2024-12-01-preview"\n}'}
          autoComplete="off"
          spellCheck={false}
          className="font-mono text-xs"
        />
        <details className="text-xs text-muted-foreground">
          <summary className="cursor-pointer select-none">
            {t("customHeadersFallback")}
          </summary>
          <div className="mt-2 space-y-1.5">
            <Label className="text-xs">{t("apiKeyLabel")}</Label>
            <MaskedKeyInput
              value={value}
              onChange={onChange}
              placeholder={
                hasKey ? t("apiKeyPlaceholderSaved") : t("apiKeyPlaceholder")
              }
              trailingAction={trailingAction}
            />
          </div>
        </details>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <Label className="flex items-center gap-1.5 text-sm">
        <IconKey className="size-3.5" />
        {t("apiKeyLabel")}
      </Label>
      <MaskedKeyInput
        value={value}
        onChange={onChange}
        placeholder={
          hasKey ? t("apiKeyPlaceholderSaved") : t("apiKeyPlaceholder")
        }
        trailingAction={trailingAction}
      />
    </div>
  );
}

function MaskedKeyInput({
  value,
  onChange,
  placeholder,
  trailingAction,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  trailingAction?: ReactNode;
}) {
  return (
    <div className="relative">
      <Input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        autoCorrect="off"
        spellCheck={false}
        className={trailingAction ? "pr-12" : undefined}
      />
      {trailingAction ? (
        <div className="absolute right-1 top-1/2 -translate-y-1/2 flex items-center">
          {trailingAction}
        </div>
      ) : null}
    </div>
  );
}
