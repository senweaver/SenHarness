"use client";

import { useEffect, useState } from "react";

export interface CountdownValue {
  /** Whole remaining minutes (floor). */
  minutes: number;
  /** Remaining seconds within the current minute (0-59). */
  seconds: number;
  /** Total milliseconds left. Negative once expired. */
  totalMs: number;
  /** True once ``expiresAt`` is in the past. */
  expired: boolean;
  /** Convenience pre-formatted ``mm:ss`` (or ``--:--`` if input was blank). */
  label: string;
}

/**
 * `useCountdown` — ticks once per second toward ``expiresAt``.
 *
 * Accepts an ISO timestamp or a ``Date``. Returns a stable, already-formatted
 * countdown shape so callers don't need to repeat the min/sec math. When the
 * input is nullish, returns a sentinel ("--:--", expired=true) so cards still
 * render deterministically.
 *
 * Intentionally uses `setInterval` + wall-clock diff rather than a naive
 * decrement so tab-throttling (`requestAnimationFrame` pauses) doesn't drift
 * the value.
 */
export function useCountdown(expiresAt: string | Date | null | undefined): CountdownValue {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!expiresAt) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [expiresAt]);

  if (!expiresAt) {
    return {
      minutes: 0,
      seconds: 0,
      totalMs: 0,
      expired: true,
      label: "--:--",
    };
  }

  const target = expiresAt instanceof Date ? expiresAt.getTime() : Date.parse(expiresAt);
  const totalMs = target - now;
  const expired = totalMs <= 0;
  const clamped = Math.max(0, totalMs);
  const minutes = Math.floor(clamped / 60000);
  const seconds = Math.floor((clamped % 60000) / 1000);
  const label = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  return { minutes, seconds, totalMs, expired, label };
}
