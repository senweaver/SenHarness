"use client";

/**
 * Adapted from Vercel AI SDK AI Elements (`Branch` primitive).
 *
 * Lightweight pager for displaying multiple alternative drafts of a single
 * turn — e.g. when the agent regenerates and the user wants to compare
 * before committing, or when a checkpoint rewind produces a sibling fork.
 *
 * Pure UI: state lives in a tiny context, the parent decides what each
 * branch contains. Hide the selector when there's only one branch by
 * setting ``totalBranches`` to 1.
 *
 * Usage:
 *
 *     <Branch defaultBranch={0}>
 *       <BranchMessages>
 *         <Message ...>v1</Message>
 *         <Message ...>v2</Message>
 *       </BranchMessages>
 *       <BranchSelector from="assistant">
 *         <BranchPrevious />
 *         <BranchPage />
 *         <BranchNext />
 *       </BranchSelector>
 *     </Branch>
 */

import { IconChevronLeft, IconChevronRight } from "@tabler/icons-react";
import {
  Children,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ComponentProps,
  type HTMLAttributes,
  type ReactElement,
  type ReactNode,
} from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface BranchContextValue {
  current: number;
  total: number;
  goPrev: () => void;
  goNext: () => void;
}

const BranchContext = createContext<BranchContextValue | null>(null);

function useBranch(): BranchContextValue {
  const ctx = useContext(BranchContext);
  if (!ctx) {
    throw new Error("Branch.* components must be used inside <Branch>");
  }
  return ctx;
}

export interface BranchProps extends HTMLAttributes<HTMLDivElement> {
  /** Initial branch index. */
  defaultBranch?: number;
  /** Notified whenever the visible branch changes. */
  onBranchChange?: (index: number) => void;
}

export function Branch({
  defaultBranch = 0,
  onBranchChange,
  className,
  children,
  ...props
}: BranchProps) {
  const [current, setCurrent] = useState(defaultBranch);
  const [total, setTotal] = useState(0);

  // Walk the children once to count BranchMessages slots so we can wrap
  // around at the boundaries without forcing the caller to thread a count.
  const branchCount = useMemo(() => {
    let n = 0;
    Children.forEach(children, (child) => {
      const el = child as ReactElement<{ children?: ReactNode }>;
      if (
        el?.type &&
        (el.type as { displayName?: string })?.displayName === "BranchMessages"
      ) {
        const subChildren = Children.toArray(el.props?.children);
        n = Math.max(n, subChildren.length);
      }
    });
    return n;
  }, [children]);

  useEffect(() => {
    setTotal(branchCount);
  }, [branchCount]);

  const change = (next: number) => {
    setCurrent(next);
    onBranchChange?.(next);
  };

  const ctx: BranchContextValue = {
    current,
    total,
    goPrev: () => change(current > 0 ? current - 1 : Math.max(total - 1, 0)),
    goNext: () => change(current < total - 1 ? current + 1 : 0),
  };

  return (
    <BranchContext.Provider value={ctx}>
      <div className={cn("grid w-full gap-2", className)} {...props}>
        {children}
      </div>
    </BranchContext.Provider>
  );
}

export type BranchMessagesProps = HTMLAttributes<HTMLDivElement>;

export function BranchMessages({ children, ...props }: BranchMessagesProps) {
  const { current } = useBranch();
  const arr = Children.toArray(children);
  return (
    <>
      {arr.map((child, idx) => (
        <div
          key={idx}
          className={cn(idx === current ? "block" : "hidden")}
          {...props}
        >
          {child}
        </div>
      ))}
    </>
  );
}
BranchMessages.displayName = "BranchMessages";

export interface BranchSelectorProps extends HTMLAttributes<HTMLDivElement> {
  /** Anchors the selector to the user / assistant gutter. */
  from: "user" | "assistant";
}

export function BranchSelector({
  className,
  from,
  ...props
}: BranchSelectorProps) {
  const { total } = useBranch();
  if (total <= 1) return null;
  return (
    <div
      className={cn(
        "flex items-center gap-1 self-end px-2 text-[11px] sh-muted",
        from === "assistant" ? "justify-start" : "justify-end",
        className,
      )}
      {...props}
    />
  );
}

export type BranchPreviousProps = ComponentProps<typeof Button>;

export function BranchPrevious({
  className,
  children,
  ...props
}: BranchPreviousProps) {
  const { goPrev, total } = useBranch();
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      aria-label="Previous branch"
      disabled={total <= 1}
      onClick={goPrev}
      className={cn("size-6 rounded-full", className)}
      {...props}
    >
      {children ?? <IconChevronLeft className="size-3.5" />}
    </Button>
  );
}

export type BranchNextProps = ComponentProps<typeof Button>;

export function BranchNext({
  className,
  children,
  ...props
}: BranchNextProps) {
  const { goNext, total } = useBranch();
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      aria-label="Next branch"
      disabled={total <= 1}
      onClick={goNext}
      className={cn("size-6 rounded-full", className)}
      {...props}
    >
      {children ?? <IconChevronRight className="size-3.5" />}
    </Button>
  );
}

export type BranchPageProps = HTMLAttributes<HTMLSpanElement>;

export function BranchPage({ className, ...props }: BranchPageProps) {
  const { current, total } = useBranch();
  return (
    <span
      className={cn("font-mono tabular-nums sh-muted", className)}
      {...props}
    >
      {current + 1} / {total}
    </span>
  );
}
