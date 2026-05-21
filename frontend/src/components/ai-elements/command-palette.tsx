"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`PromptInputCommand` family).
 * Provides the two trigger-aware popovers the SenHarness composer needs:
 *
 *   - ``<SlashCommandPalette>``  — fires when the user types ``/`` at the
 *     start of a line. Shows quick-actions (clear / regenerate / plan /
 *     research) plus per-agent skills loaded via ``useAgentSkills``.
 *
 *   - ``<MentionPalette>``        — fires when the user types ``@`` after
 *     whitespace. Shows three groups: agents / knowledge / file.
 *
 * Both palettes are *parent-controlled*: filtering and highlight state
 * live in the parent (``ChatInput``) so the textarea's ``onKeyDown`` can
 * drive ↑/↓ / Tab / Enter without focus ever leaving the editor. cmdk
 * was previously used here but its keyboard nav requires the
 * ``Command.Input`` to be focused; our composer keeps focus on the
 * textarea, so cmdk's navigation never fired and we silently shipped a
 * mouse-only palette. The current shell is a tiny manual list — small
 * enough to read, fast enough to filter, and gives the textarea a
 * single keyboard model (Tab / Enter accept, Esc dismiss).
 */

import {
  IconCommand,
  IconFile,
  IconFolder,
  IconPuzzle,
  IconRobot,
} from "@tabler/icons-react";
import {
  forwardRef,
  useImperativeHandle,
  useMemo,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";

import { cn } from "@/lib/utils";

// ─── Item types ────────────────────────────────────────────
/** Slash item categories the palette knows how to group + render.
 *
 *  Two top-level groups — same shape as the ``@`` palette:
 *    • **Skills**   (``skill_bundled`` / ``skill_workspace``)
 *      — workspace overrides and stock skills are rendered together
 *        under a single "Skills" heading. The source distinction is
 *        kept on the kind so callers that care can still tell them
 *        apart (e.g. a future "(workspace)" tag), but the heading is
 *        unified to reduce palette cognitive load.
 *    • **Commands** (``command``)
 *      — composer-side actions like ``/clear`` and ``/regenerate``
 *        that the parent wires up via ``onCommand``. Distinct from
 *        skills because they don't get inserted as text — they
 *        *trigger* something on the chat surface and clear the input.
 *
 *  ``quick`` is the old name for the same idea; kept in the union for
 *  back-compat with any caller still constructing the old shape — it
 *  buckets into Commands at render time. */
export type SlashItemKind =
  | "quick"
  | "command"
  | "skill_bundled"
  | "skill_workspace";

export interface SlashItem {
  /** Stable key — prefer ``quick:<name>`` / ``skill:<slug>`` for clarity. */
  id: string;
  /** Token inserted at the trigger location (without the leading ``/``). */
  token: string;
  /** Visible label on the row. */
  label: string;
  /** One-line subtitle. */
  description?: string;
  kind: SlashItemKind;
  /** Optional hint shown on the right (Cmd+/, etc.). */
  shortcut?: string;
}

export type MentionGroup = "agent" | "knowledge" | "file";

export interface MentionItem {
  id: string;
  /** Token inserted (without the leading ``@``). */
  token: string;
  label: string;
  description?: string;
  group: MentionGroup;
  /** Avatar URL (e.g. agent.avatar_url). */
  avatarUrl?: string | null;
}

// ─── Imperative handle ─────────────────────────────────────
/** Methods the parent ChatInput calls from the textarea's onKeyDown so
 *  ↑/↓ / Tab / Enter can drive the popover without focus leaving the
 *  editor. ``acceptHighlighted`` returns true if a row was picked
 *  (parent should preventDefault), false if the popover was empty. */
export interface PaletteHandle {
  next: () => void;
  prev: () => void;
  acceptHighlighted: () => boolean;
}

// ─── Filtering ─────────────────────────────────────────────
function matches(haystack: string, needle: string): boolean {
  if (!needle) return true;
  return haystack.toLowerCase().includes(needle.toLowerCase());
}

function filterSlash(items: SlashItem[], query: string): SlashItem[] {
  return items.filter((i) =>
    matches(`${i.token} ${i.label} ${i.description ?? ""}`, query),
  );
}

function filterMention(items: MentionItem[], query: string): MentionItem[] {
  return items.filter((i) =>
    matches(`${i.token} ${i.label} ${i.description ?? ""}`, query),
  );
}

// ─── Shell ─────────────────────────────────────────────────
interface PaletteShellProps {
  open: boolean;
  label: string;
  emptyHint: string;
  /** Total count post-filter. Drives the empty state. */
  count: number;
  /** Localised footer (↑↓ Tab Enter Esc). */
  kbdHint: string;
  className?: string;
  children: ReactNode;
}

function PaletteShell({
  open,
  label,
  emptyHint,
  count,
  kbdHint,
  className,
  children,
}: PaletteShellProps) {
  if (!open) return null;
  return (
    <div
      className={cn(
        "z-50 w-72 overflow-hidden rounded-md border sh-card shadow-xl",
        className,
      )}
      data-testid="command-palette"
      role="listbox"
      aria-label={label}
    >
      <div className="max-h-72 overflow-y-auto p-1 text-sm">
        {count === 0 ? (
          <p className="px-2 py-3 text-center text-xs sh-muted">
            {emptyHint}
          </p>
        ) : (
          children
        )}
      </div>
      {/* Keyboard footer — always visible while the palette is open so
          users discover Tab acceptance and Esc dismiss without hunting
          for documentation. Pure presentation; the actual keys are
          intercepted by the parent textarea's ``onKeyDown``. */}
      <div className="flex items-center justify-end gap-1 border-t bg-black/5 px-2 py-1 text-[10px] sh-muted dark:bg-white/5">
        <span data-testid="palette-kbd-hint">{kbdHint}</span>
      </div>
    </div>
  );
}

interface GroupProps {
  heading: string;
  children: ReactNode;
}
function PaletteGroup({ heading, children }: GroupProps) {
  return (
    <div className="py-0.5">
      <p className="px-2 py-1 text-[10px] uppercase tracking-wider sh-muted">
        {heading}
      </p>
      {children}
    </div>
  );
}

// ─── Slash palette ─────────────────────────────────────────
interface SlashPaletteProps {
  open: boolean;
  query: string;
  /** Skill + command rows, already merged by the caller. */
  items: SlashItem[];
  /** Fires when the user picks a row (click or Enter / Tab from parent). */
  onPick: (item: SlashItem) => void;
  /** Localised "no matches" copy. */
  emptyHint?: string;
  /** Localised footer "↑↓ select · Tab/Enter accept · Esc close". */
  kbdHint: string;
  /** Currently highlighted row id (parent owns the cursor). */
  highlightedId: string | null;
  /** Notify parent of hover-driven highlight changes. */
  onHighlightChange: (id: string | null) => void;
  /** Localised group headings. Single source of truth so callers can
   *  keep them parity-aligned across locales. */
  headings: {
    skills: string;
    commands: string;
  };
  /** Localised "no skills enabled yet" guidance, rendered when the
   *  underlying ``items`` list contains no skills (commands may still
   *  exist). Stays as plain text — there's no "Browse more skills"
   *  click target because routing the user to a separate settings
   *  page interrupted the chat with no in-place value. */
  noSkillsHint?: {
    title: string;
    description?: string;
  };
  className?: string;
}

export const SlashCommandPalette = forwardRef<PaletteHandle, SlashPaletteProps>(
  function SlashCommandPalette(
    {
      open,
      query,
      items,
      onPick,
      emptyHint = "No matches",
      kbdHint,
      highlightedId,
      onHighlightChange,
      headings,
      noSkillsHint,
      className,
    },
    ref,
  ) {
    const filtered = useMemo(() => filterSlash(items, query), [items, query]);
    useImperativeHandle(
      ref,
      () =>
        buildHandle(filtered, highlightedId, onHighlightChange, (item) =>
          onPick(item as SlashItem),
        ),
      [filtered, highlightedId, onHighlightChange, onPick],
    );

    // Two visible groups — same shape as the ``@`` palette:
    //   • Skills   = workspace overrides + bundled stock skills
    //   • Commands = composer-side actions (clear / regenerate / …)
    // Workspace and bundled used to be split into their own headings
    // but the distinction was noise for end users; we now group both
    // under "Skills" and rely on the row label / description if anyone
    // needs to tell them apart.
    const skills = filtered.filter(
      (i) => i.kind === "skill_workspace" || i.kind === "skill_bundled",
    );
    const commands = filtered.filter(
      (i) => i.kind === "command" || i.kind === "quick",
    );
    // Show the "no skills installed" guidance only on the unfiltered
    // initial open — once the user starts typing, the regular
    // ``emptyHint`` handles non-matching queries.
    const skillsAvailable = items.some(
      (i) => i.kind === "skill_workspace" || i.kind === "skill_bundled",
    );
    const showNoSkillsHint =
      noSkillsHint !== undefined && !skillsAvailable && query.length === 0;

    return (
      <PaletteShell
        open={open}
        label="Slash command palette"
        emptyHint={emptyHint}
        count={filtered.length + (showNoSkillsHint ? 1 : 0)}
        kbdHint={kbdHint}
        className={className}
      >
        {skills.length > 0 ? (
          <PaletteGroup heading={headings.skills}>
            {skills.map((item) => (
              <SlashRow
                key={item.id}
                item={item}
                icon={<IconPuzzle className="size-3" />}
                selected={item.id === highlightedId}
                onHover={() => onHighlightChange(item.id)}
                onPick={onPick}
              />
            ))}
          </PaletteGroup>
        ) : null}
        {showNoSkillsHint && noSkillsHint ? (
          <div
            className="px-2 py-2 text-[11px] sh-muted"
            data-testid="slash-no-skills-hint"
          >
            <p className="font-medium text-foreground/80">
              {noSkillsHint.title}
            </p>
            {noSkillsHint.description ? (
              <p className="mt-0.5 leading-relaxed">
                {noSkillsHint.description}
              </p>
            ) : null}
          </div>
        ) : null}
        {commands.length > 0 ? (
          <PaletteGroup heading={headings.commands}>
            {commands.map((item) => (
              <SlashRow
                key={item.id}
                item={item}
                icon={<IconCommand className="size-3" />}
                selected={item.id === highlightedId}
                onHover={() => onHighlightChange(item.id)}
                onPick={onPick}
              />
            ))}
          </PaletteGroup>
        ) : null}
      </PaletteShell>
    );
  },
);

interface SlashRowProps {
  item: SlashItem;
  icon: ReactNode;
  selected: boolean;
  onHover: () => void;
  onPick: (item: SlashItem) => void;
}

function SlashRow({ item, icon, selected, onHover, onPick }: SlashRowProps) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onMouseEnter={onHover}
      onMouseDown={(e) => {
        // ``mousedown`` so we fire before the textarea's blur handler,
        // which would otherwise close the palette before the click
        // reaches its target.
        e.preventDefault();
        onPick(item);
      }}
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-left",
        selected
          ? "bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]"
          : "hover:bg-black/5 dark:hover:bg-white/5",
      )}
      data-testid={`slash-row-${item.token}`}
    >
      <span className="flex size-5 shrink-0 items-center justify-center rounded bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]">
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate font-mono text-[11px]">/{item.token}</span>
        {item.description ? (
          <span className="truncate text-[10px] sh-muted">
            {item.description}
          </span>
        ) : null}
      </span>
      {item.shortcut ? (
        <span className="rounded border px-1 text-[10px] sh-muted">
          {item.shortcut}
        </span>
      ) : null}
    </button>
  );
}

// ─── Mention palette ───────────────────────────────────────
interface MentionPaletteProps {
  open: boolean;
  query: string;
  items: MentionItem[];
  onPick: (item: MentionItem) => void;
  emptyHint?: string;
  kbdHint: string;
  highlightedId: string | null;
  onHighlightChange: (id: string | null) => void;
  className?: string;
}

export const MentionPalette = forwardRef<PaletteHandle, MentionPaletteProps>(
  function MentionPalette(
    {
      open,
      query,
      items,
      onPick,
      emptyHint = "No matches",
      kbdHint,
      highlightedId,
      onHighlightChange,
      className,
    },
    ref,
  ) {
    const filtered = useMemo(
      () => filterMention(items, query),
      [items, query],
    );
    useImperativeHandle(
      ref,
      () =>
        buildHandle(filtered, highlightedId, onHighlightChange, (item) =>
          onPick(item as MentionItem),
        ),
      [filtered, highlightedId, onHighlightChange, onPick],
    );

    const agents = filtered.filter((i) => i.group === "agent");
    const knowledge = filtered.filter((i) => i.group === "knowledge");
    const files = filtered.filter((i) => i.group === "file");

    return (
      <PaletteShell
        open={open}
        label="Mention palette"
        emptyHint={emptyHint}
        count={filtered.length}
        kbdHint={kbdHint}
        className={className}
      >
        {agents.length > 0 ? (
          <PaletteGroup heading="Agents">
            {agents.map((item) => (
              <MentionRow
                key={item.id}
                item={item}
                selected={item.id === highlightedId}
                onHover={() => onHighlightChange(item.id)}
                onPick={onPick}
              />
            ))}
          </PaletteGroup>
        ) : null}
        {knowledge.length > 0 ? (
          <PaletteGroup heading="Knowledge">
            {knowledge.map((item) => (
              <MentionRow
                key={item.id}
                item={item}
                selected={item.id === highlightedId}
                onHover={() => onHighlightChange(item.id)}
                onPick={onPick}
              />
            ))}
          </PaletteGroup>
        ) : null}
        {files.length > 0 ? (
          <PaletteGroup heading="Files">
            {files.map((item) => (
              <MentionRow
                key={item.id}
                item={item}
                selected={item.id === highlightedId}
                onHover={() => onHighlightChange(item.id)}
                onPick={onPick}
              />
            ))}
          </PaletteGroup>
        ) : null}
      </PaletteShell>
    );
  },
);

interface MentionRowProps {
  item: MentionItem;
  selected: boolean;
  onHover: () => void;
  onPick: (item: MentionItem) => void;
}

function MentionRow({ item, selected, onHover, onPick }: MentionRowProps) {
  const Icon = iconForMentionGroup(item.group);
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      onMouseEnter={onHover}
      onMouseDown={(e) => {
        e.preventDefault();
        onPick(item);
      }}
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-left",
        selected
          ? "bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]"
          : "hover:bg-black/5 dark:hover:bg-white/5",
      )}
      data-testid={`mention-row-${item.group}-${item.token}`}
    >
      {item.avatarUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={item.avatarUrl}
          alt=""
          className="size-5 shrink-0 rounded-full object-cover"
        />
      ) : (
        <span className="flex size-5 shrink-0 items-center justify-center rounded bg-[rgb(var(--color-primary))]/10 text-[rgb(var(--color-primary))]">
          <Icon className="size-3" />
        </span>
      )}
      <span className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-[12px]">{item.label}</span>
        {item.description ? (
          <span className="truncate text-[10px] sh-muted">
            {item.description}
          </span>
        ) : null}
      </span>
    </button>
  );
}

function iconForMentionGroup(group: MentionGroup) {
  switch (group) {
    case "agent":
      return IconRobot;
    case "knowledge":
      return IconFolder;
    case "file":
    default:
      return IconFile;
  }
}

// ─── Imperative handle factory ─────────────────────────────
type AnyItem = { id: string };

function buildHandle(
  filtered: AnyItem[],
  highlightedId: string | null,
  onHighlightChange: (id: string | null) => void,
  onAccept: (item: AnyItem) => void,
): PaletteHandle {
  const idx = highlightedId
    ? filtered.findIndex((i) => i.id === highlightedId)
    : -1;
  return {
    next: () => {
      if (filtered.length === 0) return;
      const nextIdx = idx < 0 ? 0 : (idx + 1) % filtered.length;
      onHighlightChange(filtered[nextIdx]!.id);
    },
    prev: () => {
      if (filtered.length === 0) return;
      const prevIdx =
        idx <= 0 ? filtered.length - 1 : (idx - 1) % filtered.length;
      onHighlightChange(filtered[prevIdx]!.id);
    },
    acceptHighlighted: () => {
      if (filtered.length === 0) return false;
      const target = idx >= 0 ? filtered[idx]! : filtered[0]!;
      onAccept(target);
      return true;
    },
  };
}

// Re-export for callers that wanted a typed PropsOf helper.
export type { ComponentPropsWithoutRef as PropsOf };
