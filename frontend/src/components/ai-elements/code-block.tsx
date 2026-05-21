"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`CodeBlock` primitive).
 *
 * Lightweight, dependency-free code block with an optional copy button.
 * Why no syntax highlighter here?
 *   - The chat ``<Response>`` already pipes through ``streamdown``, which
 *     ships a Shiki-backed code renderer for inline assistant code blocks.
 *   - This primitive is for *standalone* code chunks: tool output, JSON
 *     payloads, raw API responses, settings snippets, etc. — places where a
 *     monospaced + scrollable + copyable affordance is enough and pulling
 *     in a 200 kB highlighter is overkill.
 *
 * Use ``<Response>`` when the source is Markdown-with-fenced-blocks. Use
 * ``<CodeBlock>`` when you have a raw string and want a tidy box.
 */

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ComponentProps,
  type HTMLAttributes,
  type ReactNode,
} from "react";
import { IconCheck, IconCopy } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CodeBlockContextValue {
  code: string;
}

const CodeBlockContext = createContext<CodeBlockContextValue>({ code: "" });

export interface CodeBlockProps extends HTMLAttributes<HTMLDivElement> {
  /** The raw code string to render. */
  code: string;
  /** Optional language tag — surfaced as a small header chip + ``data-`` attr. */
  language?: string;
  /** Render the code with line numbers in a leading gutter. */
  showLineNumbers?: boolean;
  /** Optional toolbar slot (typically the ``<CodeBlockCopyButton>``). */
  children?: ReactNode;
}

export function CodeBlock({
  code,
  language,
  showLineNumbers = false,
  className,
  children,
  ...props
}: CodeBlockProps) {
  const lines = code.split("\n");
  return (
    <CodeBlockContext.Provider value={{ code }}>
      <div
        className={cn(
          "group relative w-full overflow-hidden rounded-md border bg-[rgb(var(--color-card))] text-[rgb(var(--color-fg))]",
          className,
        )}
        data-language={language ?? undefined}
        {...props}
      >
        {language || children ? (
          <div className="flex items-center justify-between gap-2 border-b bg-black/[0.025] px-2 py-1 text-[10px] uppercase tracking-wider sh-muted dark:bg-white/[0.04]">
            <span className="font-mono">{language ?? "code"}</span>
            <span className="flex items-center gap-1">{children}</span>
          </div>
        ) : null}
        <pre className="m-0 max-h-[28rem] overflow-auto p-3 text-xs leading-relaxed">
          <code className="font-mono">
            {showLineNumbers ? (
              lines.map((line, i) => (
                <span key={i} className="grid grid-cols-[3ch_1fr] gap-3">
                  <span className="select-none text-right sh-muted">
                    {i + 1}
                  </span>
                  <span>{line || "\u200B"}</span>
                </span>
              ))
            ) : (
              code
            )}
          </code>
        </pre>
      </div>
    </CodeBlockContext.Provider>
  );
}

export interface CodeBlockCopyButtonProps
  extends Omit<ComponentProps<typeof Button>, "onError"> {
  onCopy?: () => void;
  onError?: (err: Error) => void;
  /** ms to keep the success state visible. */
  timeout?: number;
}

export function CodeBlockCopyButton({
  onCopy,
  onError,
  timeout = 2000,
  children,
  className,
  ...props
}: CodeBlockCopyButtonProps) {
  const [isCopied, setIsCopied] = useState(false);
  const { code } = useContext(CodeBlockContext);

  const copyToClipboard = useCallback(() => {
    const succeed = () => {
      setIsCopied(true);
      onCopy?.();
      window.setTimeout(() => setIsCopied(false), timeout);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard
        .writeText(code)
        .then(succeed)
        .catch((err: unknown) => onError?.(err as Error));
      return;
    }
    // Fallback for non-secure contexts.
    try {
      const ta = document.createElement("textarea");
      ta.value = code;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      succeed();
    } catch (err) {
      onError?.(err as Error);
    }
  }, [code, onCopy, onError, timeout]);

  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      className={cn("size-6 shrink-0", className)}
      onClick={copyToClipboard}
      title={isCopied ? "Copied" : "Copy"}
      aria-label="Copy code"
      {...props}
    >
      {children ?? (isCopied ? (
        <IconCheck className="size-3.5 text-green-500" />
      ) : (
        <IconCopy className="size-3.5" />
      ))}
    </Button>
  );
}
