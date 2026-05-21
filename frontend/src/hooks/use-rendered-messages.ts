"use client";

import { useEffect, useReducer } from "react";

import type { UIMessage } from "ai";

/**
 * Force the parent component to re-read ``useChat``'s latest message
 * snapshot on every animation frame while a turn is active.
 *
 * The AI SDK subscribes React via ``useSyncExternalStore``; under our
 * WebSocket transport bursts of text-delta chunks coalesce into a single
 * React commit — empirically 159 chunks arriving evenly over 7.6 s were
 * collapsed into 7 renders, all within the first 315 ms. The result is
 * the "first paragraph streams, then the rest dumps" symptom users see.
 *
 * Bumping a tick reducer at ~60 fps schedules a fresh commit each frame.
 * ``useChat`` (called in the parent) returns the latest store snapshot
 * on every render, so the streaming text grows by whatever deltas have
 * accumulated since the last frame.  When ``isActive`` flips false the
 * loop stops and we fall back to the SDK's own notify cadence.
 */
export function useRenderedMessages<T extends UIMessage>(
  rawMessages: T[],
  isActive: boolean,
): T[] {
  const [, tick] = useReducer((n: number) => (n + 1) & 0xffffff, 0);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    let raf = 0;
    const loop = () => {
      if (cancelled) return;
      tick();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => {
      cancelled = true;
      cancelAnimationFrame(raf);
    };
  }, [isActive]);

  return rawMessages;
}
