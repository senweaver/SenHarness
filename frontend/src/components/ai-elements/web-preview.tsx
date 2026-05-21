"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`WebPreview` primitive).
 *
 * Sandboxed iframe panel with an editable URL bar — useful for showing the
 * runtime preview of a harness ``write_file`` or ``generate_html`` artifact
 * without leaving the workspace pane. The iframe is locked down with a
 * conservative ``sandbox`` attribute (same default as the upstream
 * primitive) so untrusted harness output can't escape the frame.
 *
 * Composition:
 *
 *     <WebPreview defaultUrl="/preview/index.html">
 *       <WebPreviewNavigation>
 *         <WebPreviewUrl />
 *       </WebPreviewNavigation>
 *       <WebPreviewBody />
 *     </WebPreview>
 */

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ComponentProps,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

interface WebPreviewContextValue {
  url: string;
  setUrl: (url: string) => void;
}

const WebPreviewContext = createContext<WebPreviewContextValue | null>(null);

function useWebPreview(): WebPreviewContextValue {
  const ctx = useContext(WebPreviewContext);
  if (!ctx) {
    throw new Error("WebPreview.* components must be used inside <WebPreview>");
  }
  return ctx;
}

export interface WebPreviewProps extends ComponentProps<"div"> {
  defaultUrl?: string;
  onUrlChange?: (url: string) => void;
}

export function WebPreview({
  defaultUrl = "",
  onUrlChange,
  className,
  children,
  ...props
}: WebPreviewProps) {
  const [url, setUrl] = useState(defaultUrl);

  const handleUrlChange = useCallback(
    (next: string) => {
      setUrl(next);
      onUrlChange?.(next);
    },
    [onUrlChange],
  );

  return (
    <WebPreviewContext.Provider value={{ url, setUrl: handleUrlChange }}>
      <div
        className={cn(
          "flex size-full flex-col rounded-lg border bg-[rgb(var(--color-card))]",
          className,
        )}
        {...props}
      >
        {children}
      </div>
    </WebPreviewContext.Provider>
  );
}

export type WebPreviewNavigationProps = ComponentProps<"div">;

export function WebPreviewNavigation({
  className,
  ...props
}: WebPreviewNavigationProps) {
  return (
    <div
      className={cn("flex items-center gap-1 border-b p-2", className)}
      {...props}
    />
  );
}

export type WebPreviewUrlProps = Omit<ComponentProps<typeof Input>, "ref">;

export function WebPreviewUrl({
  value,
  onKeyDown,
  ...props
}: WebPreviewUrlProps) {
  const { url, setUrl } = useWebPreview();

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      const target = event.target as HTMLInputElement;
      setUrl(target.value);
    }
    onKeyDown?.(event);
  };

  return (
    <Input
      className="h-8 flex-1 text-xs"
      placeholder="Enter URL…"
      value={value ?? url}
      onChange={(e) => setUrl(e.target.value)}
      onKeyDown={handleKeyDown}
      {...props}
    />
  );
}

export type WebPreviewBodyProps = ComponentProps<"iframe"> & {
  loading?: ReactNode;
};

export function WebPreviewBody({
  className,
  loading,
  src,
  sandbox = "allow-scripts allow-same-origin allow-forms allow-popups allow-presentation",
  ...props
}: WebPreviewBodyProps) {
  const { url } = useWebPreview();
  return (
    <div className="relative flex-1">
      <iframe
        title="preview"
        className={cn("size-full", className)}
        sandbox={sandbox}
        src={(src ?? url) || undefined}
        {...props}
      />
      {loading ? (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
          {loading}
        </div>
      ) : null}
    </div>
  );
}
