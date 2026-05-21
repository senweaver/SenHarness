"use client";

import { useEffect, useState } from "react";
import { IconChevronDown } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { cn } from "@/lib/utils";

interface SetupGuideProps {
  /** Channel ``kind`` — drives the i18n lookup ``settings.channels.guide.<kind>``. */
  kind: string;
  className?: string;
}

const STORAGE_KEY_PREFIX = "senharness:channelGuide:dismissed:";

/**
 * Friendly numbered setup steps shown above the channel-create form.
 *
 * UX contract: the first time an operator picks a given kind, the
 * panel auto-expands so they can see what they're getting into. After
 * they collapse it (the panel writes ``1`` to localStorage), every
 * subsequent visit defaults to collapsed — they can still re-open by
 * clicking the header.
 *
 * Renders nothing if the host hasn't shipped i18n entries for this
 * kind, so dropping the component into a layout never produces an
 * empty "see steps" placeholder for unknown kinds.
 */
export function SetupGuide({ kind, className }: SetupGuideProps) {
  const t = useTranslations("settings.channels.guide");

  const introKey = `${kind}.intro`;
  const stepsKey = `${kind}.steps`;
  const hasGuide = t.has(introKey);

  // Default-open state needs to read localStorage, but only on the
  // client. Start collapsed and flip to open in the effect for SSR
  // safety.
  const [open, setOpen] = useState(false);
  const [hasResolvedDefault, setHasResolvedDefault] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const dismissed =
      window.localStorage.getItem(STORAGE_KEY_PREFIX + kind) === "1";
    setOpen(!dismissed);
    setHasResolvedDefault(true);
  }, [kind]);

  if (!hasGuide) return null;

  const steps = (() => {
    try {
      const raw = t.raw(stepsKey);
      return Array.isArray(raw) ? (raw as string[]) : [];
    } catch {
      return [];
    }
  })();

  const handleToggle = (next: boolean) => {
    setOpen(next);
    if (typeof window !== "undefined") {
      // We persist the *dismissed* state — collapsing once means the
      // user has seen the steps and doesn't want them in their face
      // every time they edit this channel.
      window.localStorage.setItem(
        STORAGE_KEY_PREFIX + kind,
        next ? "0" : "1",
      );
    }
  };

  return (
    <div
      className={cn(
        "rounded-md border bg-[rgb(var(--color-muted))]/40 text-xs",
        className,
      )}
      // ``hidden`` hint while we resolve the localStorage default keeps
      // the layout from flashing open-then-collapsed on first paint.
      data-resolved={hasResolvedDefault ? "1" : "0"}
    >
      <button
        type="button"
        onClick={() => handleToggle(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
        aria-expanded={open}
      >
        <IconChevronDown
          className={cn(
            "size-3.5 transition-transform",
            open && "rotate-180",
          )}
        />
        <span className="font-medium">{t("expand")}</span>
        {!open && t.has(introKey) && (
          <span className="ml-1 truncate text-[11px] sh-muted">
            {t(introKey as Parameters<typeof t>[0])}
          </span>
        )}
      </button>
      {open && (
        <div className="border-t px-3 py-2">
          <p className="mb-1.5 text-[11px] sh-muted">
            {t(introKey as Parameters<typeof t>[0])}
          </p>
          <ol className="ml-5 list-decimal space-y-1 text-[12px] leading-relaxed">
            {steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  );
}

export default SetupGuide;
