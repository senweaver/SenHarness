"use client";

import { useState } from "react";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CopyButtonProps {
  text: string;
  className?: string;
  size?: "sm" | "icon";
  ariaLabel?: string;
}

/**
 * Tiny copy-to-clipboard button. Shows a green check for 2 s after success.
 * The clipboard API requires a secure context (HTTPS or localhost) — falls
 * back to a temporary `<textarea>` + `execCommand('copy')` so it still works
 * in dev over plain HTTP.
 */
export function CopyButton({
  text,
  className,
  size = "icon",
  ariaLabel,
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API failures (denied permission, non-secure context
      // without the textarea fallback succeeding) leave ``copied`` at
      // false — the absent check icon is signal enough.
    }
  };

  return (
    <Button
      type="button"
      variant="ghost"
      size={size}
      className={cn(
        "h-6 w-6 p-0 transition-opacity",
        className,
      )}
      onClick={handleCopy}
      title={copied ? "Copied!" : "Copy"}
      aria-label={ariaLabel ?? (copied ? "Copied" : "Copy to clipboard")}
      data-copied={copied ? "true" : "false"}
    >
      {copied ? (
        <IconCheck className="size-3.5 text-green-500" />
      ) : (
        <IconCopy className="size-3.5" />
      )}
    </Button>
  );
}
