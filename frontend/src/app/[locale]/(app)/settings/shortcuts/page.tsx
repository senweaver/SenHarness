"use client";

import { useEffect, useState } from "react";
import { IconKeyboard } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";

interface Shortcut {
  keys: string[];
  labelKey: string; // translation key under settings.shortcuts.entries.*
  scope: "global" | "chat";
}

// Keep in sync with CommandPalette.tsx + any future kbd listeners.
const SHORTCUTS: Shortcut[] = [
  { keys: ["Ctrl", "K"], labelKey: "commandPalette", scope: "global" },
  { keys: ["Ctrl", "/"], labelKey: "quickHelp", scope: "global" },
  { keys: ["Ctrl", "B"], labelKey: "toggleSidebar", scope: "global" },
  { keys: ["Enter"], labelKey: "sendMessage", scope: "chat" },
  { keys: ["Shift", "Enter"], labelKey: "newline", scope: "chat" },
  { keys: ["Esc"], labelKey: "cancelRun", scope: "chat" },
];

export default function ShortcutsPage() {
  const t = useTranslations("settings.shortcuts");
  const [isMac, setIsMac] = useState(false);
  useEffect(() => {
    if (typeof navigator !== "undefined") {
      setIsMac(/mac/i.test(navigator.platform || navigator.userAgent));
    }
  }, []);

  const renderKey = (k: string) => {
    if (k === "Ctrl" && isMac) return "⌘";
    return k;
  };

  const globalKeys = SHORTCUTS.filter((s) => s.scope === "global");
  const chatKeys = SHORTCUTS.filter((s) => s.scope === "chat");

  return (
    <div>
      <PageHeader title={t("title")} description={t("description")} />

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <IconKeyboard className="size-4" />
              {t("global")}
            </CardTitle>
            <CardDescription>{t("globalDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {globalKeys.map((s) => (
                <li key={s.keys.join("+")} className="flex items-center py-2">
                  <span className="flex-1 text-sm">
                    {t(`entries.${s.labelKey}` as "entries.commandPalette")}
                  </span>
                  <KeyCombo keys={s.keys.map(renderKey)} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <IconKeyboard className="size-4" />
              {t("chat")}
            </CardTitle>
            <CardDescription>{t("chatDesc")}</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="divide-y">
              {chatKeys.map((s) => (
                <li key={s.keys.join("+")} className="flex items-center py-2">
                  <span className="flex-1 text-sm">
                    {t(`entries.${s.labelKey}` as "entries.sendMessage")}
                  </span>
                  <KeyCombo keys={s.keys.map(renderKey)} />
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>

      <p className="mt-4 text-[11px] sh-muted">{t("footnote")}</p>
    </div>
  );
}

function KeyCombo({ keys }: { keys: string[] }) {
  return (
    <span className="flex items-center gap-0.5">
      {keys.map((k, i) => (
        <span key={`${k}-${i}`} className="flex items-center gap-0.5">
          {i > 0 && <span className="text-[10px] sh-muted">+</span>}
          <kbd className="rounded border bg-black/5 px-1.5 py-0.5 font-mono text-[11px] dark:bg-white/10">
            {k}
          </kbd>
        </span>
      ))}
    </span>
  );
}
