"use client";

/**
 * ComposerOverlay — inline ``/skill`` and ``@mention`` highlight layer.
 *
 * Sits on top of the chat ``<textarea>`` (same box, transparent text)
 * and re-renders the same string with a coloured pill background around
 * tokens the user has actually committed (i.e. the slug exists in the
 * ``slashTokens`` / ``mentionTokens`` whitelist passed by the parent).
 *
 * Why this approach instead of contenteditable / Lexical:
 *   • Keeps the textarea as the source of truth — caret, selection,
 *     IME, voice input, autosize, accessibility all stay native.
 *   • Zero impact on form submission / paste / undo / redo.
 *   • The overlay is purely visual and ``pointer-events-none``; nothing
 *     ever blocks user input.
 *
 * Alignment guarantee:
 *   • Both surfaces share ``COMPOSER_TEXT_CLASS`` from prompt-input.tsx
 *     (padding, font-size, line-height, word-break). Edit there only.
 *   • Scroll is mirrored from the textarea via the ``scrollTop`` /
 *     ``scrollLeft`` props the parent reads off ``onScroll``.
 *
 * Whitelist semantics:
 *   • Only tokens that exist in ``slashTokens`` / ``mentionTokens`` get
 *     highlighted. ``@noreply`` typed by accident stays plain. The
 *     parent already computes these sets from the slash/mention
 *     palettes, so no extra queries.
 */

import { useMemo, type CSSProperties } from "react";

import { COMPOSER_TEXT_CLASS } from "@/components/ai-elements";
import { cn } from "@/lib/utils";

interface ComposerOverlayProps {
  /** Live textarea value. */
  value: string;
  /** Set of recognised ``/<token>`` slugs to highlight (no leading slash). */
  slashTokens: ReadonlySet<string>;
  /** Set of recognised ``@<token>`` slugs to highlight (no leading @). */
  mentionTokens: ReadonlySet<string>;
  /** Mirrored from the textarea so the overlay stays aligned while the
   *  user scrolls a long composition. */
  scrollTop: number;
  scrollLeft: number;
  className?: string;
}

/** Single token segment yielded by ``parseTokens``. */
type Segment =
  | { kind: "text"; text: string }
  | { kind: "slash"; text: string }
  | { kind: "mention"; text: string };

/** Matches a leading-anchored ``/`` or ``@`` followed by ``[\w-]+``.
 *
 *  We re-use the same anchoring as ``recomputeTrigger`` in ChatInput:
 *    • ``/`` — start of string or right after a newline (line start)
 *    • ``@`` — start of string or right after whitespace
 *
 *  Anchoring matters: it avoids painting ``http://example.com/foo`` or
 *  ``user@host`` as if they were skills/mentions. */
const TOKEN_RE = /(?:^|(?<=\n))\/([\w-]+)|(?:^|(?<=\s))@([\w-]+)/g;

function parseTokens(
  value: string,
  slashTokens: ReadonlySet<string>,
  mentionTokens: ReadonlySet<string>,
): Segment[] {
  const out: Segment[] = [];
  let cursor = 0;
  TOKEN_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TOKEN_RE.exec(value)) !== null) {
    const [match, slashSlug, mentionSlug] = m;
    const start = m.index;
    const end = start + match.length;
    const isSlash = Boolean(slashSlug);
    const slug = (slashSlug ?? mentionSlug)!;
    const recognised = isSlash
      ? slashTokens.has(slug)
      : mentionTokens.has(slug);
    if (!recognised) {
      // Leave it as plain text so the overlay matches the textarea
      // glyph-for-glyph; we just don't paint a pill behind it.
      continue;
    }
    if (start > cursor) {
      out.push({ kind: "text", text: value.slice(cursor, start) });
    }
    out.push({ kind: isSlash ? "slash" : "mention", text: match });
    cursor = end;
  }
  if (cursor < value.length) {
    out.push({ kind: "text", text: value.slice(cursor) });
  }
  return out;
}

export function ComposerOverlay({
  value,
  slashTokens,
  mentionTokens,
  scrollTop,
  scrollLeft,
  className,
}: ComposerOverlayProps) {
  const segments = useMemo(
    () => parseTokens(value, slashTokens, mentionTokens),
    [value, slashTokens, mentionTokens],
  );

  // Trailing newline rendering quirk: an HTML block with text ending in
  // ``\n`` collapses the visual newline. Append a zero-width space so
  // the overlay's last "row" still occupies the same vertical space as
  // the textarea's last row (which always reserves room for the caret).
  const tail = value.endsWith("\n") ? "\u200b" : "";

  const style: CSSProperties = {
    // Negative scroll offset moves the rendered text under the
    // viewport just like the textarea does internally — keeps tokens
    // aligned with their source glyphs while the user scrolls.
    transform: `translate(${-scrollLeft}px, ${-scrollTop}px)`,
  };

  return (
    <div
      aria-hidden="true"
      data-testid="composer-overlay"
      className={cn(
        COMPOSER_TEXT_CLASS,
        // Pin the overlay to the textarea's box. ``pointer-events-none``
        // and ``select-none`` keep every cursor / selection action with
        // the underlying textarea.
        "pointer-events-none absolute inset-0 select-none overflow-hidden text-transparent",
        // Match the textarea's own ``shadow-none focus-visible:ring-0``
        // overrides so the box model lines up.
        "shadow-none",
        className,
      )}
      style={style}
    >
      {segments.map((seg, i) => {
        if (seg.kind === "text") {
          return <span key={i}>{seg.text}</span>;
        }
        if (seg.kind === "slash") {
          return (
            <span
              key={i}
              data-token-kind="slash"
              className="rounded bg-[rgb(var(--color-primary))]/15 text-[rgb(var(--color-primary))]"
            >
              {seg.text}
            </span>
          );
        }
        return (
          <span
            key={i}
            data-token-kind="mention"
            className="rounded bg-emerald-500/15 text-emerald-700 dark:text-emerald-300"
          >
            {seg.text}
          </span>
        );
      })}
      {tail}
    </div>
  );
}

export { parseTokens as __parseTokensForTest };
