"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import { CopyButton } from "./CopyButton";
import { cn } from "@/lib/utils";

interface MarkdownContentProps {
  content: string;
  className?: string;
}

/**
 * Sanitised, GFM-flavoured markdown renderer used for assistant replies.
 *
 * Strategy:
 *   - `remark-gfm` for tables / strikethrough / task lists / autolinks.
 *   - `rehype-highlight` adds `hljs` class names to code blocks; the actual
 *     palette comes from `highlight.js/styles/github-dark.css` imported in
 *     `globals.css` (lazy injected on first render to avoid bloating the
 *     auth/login bundles).
 *   - `rehype-sanitize` runs on the produced HTML to strip script tags,
 *     event handlers, and unknown protocols. The default schema is
 *     extended just enough to keep the `language-*` and `hljs` class names
 *     that `rehype-highlight` writes.
 */

// Extend the default sanitize schema to allow the className attributes used
// by `rehype-highlight` (without this the `language-python` / `hljs`
// classes are stripped and code blocks render with no syntax colour).
const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultSchema.attributes,
    code: [...((defaultSchema.attributes?.code as unknown[]) ?? []), "className"],
    span: [...((defaultSchema.attributes?.span as unknown[]) ?? []), "className"],
    pre: [...((defaultSchema.attributes?.pre as unknown[]) ?? []), "className"],
    a: [
      ...((defaultSchema.attributes?.a as unknown[]) ?? []),
      "target",
      "rel",
    ],
  },
};

/** Lazy-load highlight.js stylesheet on first render in the browser. */
let _hljsCssInjected = false;
function ensureHighlightCss() {
  if (_hljsCssInjected || typeof document === "undefined") return;
  // The CSS module sits in `node_modules/highlight.js/styles/github-dark.css`;
  // we inject a <link> instead of a JS import so the browser can cache it.
  const id = "sh-hljs-stylesheet";
  if (document.getElementById(id)) {
    _hljsCssInjected = true;
    return;
  }
  const style = document.createElement("style");
  style.id = id;
  // Inlined minimal GitHub-style theme — works in both light and dark.
  // Using inline avoids a webpack import resolution change for hl.js CSS.
  style.textContent = `
.hljs{display:block;overflow-x:auto;padding:0;color:rgb(var(--color-fg));background:transparent}
.hljs-comment,.hljs-quote{color:#6a737d;font-style:italic}
.hljs-keyword,.hljs-selector-tag,.hljs-subst{color:#d73a49;font-weight:600}
.hljs-string,.hljs-doctag,.hljs-template-tag,.hljs-template-variable{color:#22863a}
.hljs-number,.hljs-literal{color:#005cc5}
.hljs-title,.hljs-section,.hljs-name,.hljs-selector-class,.hljs-selector-id,.hljs-selector-pseudo{color:#6f42c1;font-weight:600}
.hljs-attr,.hljs-attribute,.hljs-built_in,.hljs-builtin-name,.hljs-type{color:#005cc5}
.hljs-variable,.hljs-symbol,.hljs-bullet,.hljs-link,.hljs-meta{color:#e36209}
.hljs-deletion{background:#ffeef0;color:#b31d28}
.hljs-addition{background:#f0fff4;color:#22863a}
.hljs-emphasis{font-style:italic}
.hljs-strong{font-weight:bold}
.dark .hljs-comment,.dark .hljs-quote{color:#8b949e}
.dark .hljs-keyword,.dark .hljs-selector-tag,.dark .hljs-subst{color:#ff7b72}
.dark .hljs-string,.dark .hljs-doctag,.dark .hljs-template-tag,.dark .hljs-template-variable{color:#a5d6ff}
.dark .hljs-number,.dark .hljs-literal{color:#79c0ff}
.dark .hljs-title,.dark .hljs-section,.dark .hljs-name,.dark .hljs-selector-class,.dark .hljs-selector-id,.dark .hljs-selector-pseudo{color:#d2a8ff}
.dark .hljs-attr,.dark .hljs-attribute,.dark .hljs-built_in,.dark .hljs-builtin-name,.dark .hljs-type{color:#79c0ff}
.dark .hljs-variable,.dark .hljs-symbol,.dark .hljs-bullet,.dark .hljs-link,.dark .hljs-meta{color:#ffa657}
`;
  document.head.appendChild(style);
  _hljsCssInjected = true;
}

export function MarkdownContent({ content, className }: MarkdownContentProps) {
  // Inject the highlight.js theme on first render (browser only).
  if (typeof window !== "undefined") {
    ensureHighlightCss();
  }
  return (
    <div
      className={cn(
        "sh-markdown text-sm leading-relaxed break-words",
        className,
      )}
      data-testid="markdown-content"
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight, [rehypeSanitize, sanitizeSchema]]}
        components={{
          pre({ children, ...props }) {
            const codeElement = children as
              | React.ReactElement<{
                  children?: React.ReactNode;
                  className?: string;
                }>
              | undefined;
            const rawChildren = codeElement?.props?.children;
            let codeContent = "";
            if (typeof rawChildren === "string") {
              codeContent = rawChildren;
            } else if (Array.isArray(rawChildren)) {
              codeContent = rawChildren
                .map((c) => (typeof c === "string" ? c : ""))
                .join("");
            }
            const langClass = codeElement?.props?.className ?? "";
            const lang = langClass.match(/language-([a-zA-Z0-9+-]+)/)?.[1];
            return (
              <div className="group/code relative my-2">
                {lang && (
                  <div className="absolute left-2 top-1.5 text-[10px] font-mono uppercase tracking-wide sh-muted">
                    {lang}
                  </div>
                )}
                <pre
                  className="overflow-x-auto rounded-md bg-black/5 dark:bg-white/5 p-3 pt-6 text-xs"
                  {...props}
                >
                  {children}
                </pre>
                {codeContent && (
                  <div className="absolute right-2 top-2 opacity-0 group-hover/code:opacity-100 transition-opacity">
                    <CopyButton text={codeContent} />
                  </div>
                )}
              </div>
            );
          },
          code({ className, children, ...props }) {
            const isInline = !className;
            if (isInline) {
              return (
                <code
                  className="rounded bg-black/10 dark:bg-white/10 px-1.5 py-0.5 text-[12px] font-mono"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            return (
              <code className={cn("hljs", className)} {...props}>
                {children}
              </code>
            );
          },
          a({ href, children, ...props }) {
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="text-[rgb(var(--color-primary))] underline underline-offset-2 hover:opacity-80"
                {...props}
              >
                {children}
              </a>
            );
          },
          p({ children, ...props }) {
            return (
              <p className="my-2 first:mt-0 last:mb-0" {...props}>
                {children}
              </p>
            );
          },
          ul({ children, ...props }) {
            return (
              <ul
                className="my-2 ml-5 list-disc space-y-0.5 first:mt-0 last:mb-0"
                {...props}
              >
                {children}
              </ul>
            );
          },
          ol({ children, ...props }) {
            return (
              <ol
                className="my-2 ml-5 list-decimal space-y-0.5 first:mt-0 last:mb-0"
                {...props}
              >
                {children}
              </ol>
            );
          },
          li({ children, ...props }) {
            return (
              <li className="leading-snug" {...props}>
                {children}
              </li>
            );
          },
          h1({ children, ...props }) {
            return (
              <h1
                className="mt-3 mb-2 text-lg font-bold first:mt-0"
                {...props}
              >
                {children}
              </h1>
            );
          },
          h2({ children, ...props }) {
            return (
              <h2
                className="mt-3 mb-2 text-base font-bold first:mt-0"
                {...props}
              >
                {children}
              </h2>
            );
          },
          h3({ children, ...props }) {
            return (
              <h3
                className="mt-3 mb-1.5 text-sm font-bold first:mt-0"
                {...props}
              >
                {children}
              </h3>
            );
          },
          h4({ children, ...props }) {
            return (
              <h4
                className="mt-2 mb-1 text-sm font-semibold first:mt-0"
                {...props}
              >
                {children}
              </h4>
            );
          },
          blockquote({ children, ...props }) {
            return (
              <blockquote
                className="my-2 border-l-2 pl-3 italic sh-muted first:mt-0 last:mb-0"
                style={{ borderColor: "rgba(100,116,139,0.5)" }}
                {...props}
              >
                {children}
              </blockquote>
            );
          },
          table({ children, ...props }) {
            return (
              <div className="my-2 overflow-x-auto first:mt-0 last:mb-0">
                <table
                  className="min-w-full border-collapse text-xs"
                  {...props}
                >
                  {children}
                </table>
              </div>
            );
          },
          th({ children, ...props }) {
            return (
              <th
                className="border-b border-black/15 dark:border-white/15 px-2 py-1 text-left font-semibold"
                {...props}
              >
                {children}
              </th>
            );
          },
          td({ children, ...props }) {
            return (
              <td
                className="border-b border-black/10 dark:border-white/10 px-2 py-1 align-top"
                {...props}
              >
                {children}
              </td>
            );
          },
          hr({ ...props }) {
            return (
              <hr className="my-3 border-black/15 dark:border-white/15" {...props} />
            );
          },
          img({ alt, src, ...props }) {
            return (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                alt={alt ?? ""}
                src={typeof src === "string" ? src : undefined}
                className="my-2 max-h-[480px] max-w-full rounded-md border"
                loading="lazy"
                {...props}
              />
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
