/**
 * Web Vitals → backend /api/v1/metrics/web-vitals.
 *
 * Two public entry points:
 *
 *   1. ``reportWebVitals()`` — one-shot installer called during app
 *      bootstrap (``instrumentation-client.ts`` and the guarded
 *      ``(app)/layout.tsx`` both call it; it's idempotent).
 *   2. ``reportWebVital(metric)`` — per-metric beacon sender used as the
 *      callback passed to the ``web-vitals`` library.
 *
 * We POST each metric immediately (not batched) using ``navigator.sendBeacon``
 * when available so tab-close doesn't drop the final LCP / CLS measurement.
 * ``fetch`` with ``keepalive: true`` is the fallback for browsers without
 * sendBeacon. Network errors are swallowed — observability must never
 * surface as a user-visible failure.
 */

import type { Metric } from "web-vitals";

const API_BASE =
    process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ??
    "http://localhost:8000";

/**
 * Cached once per tab for group-by-session visualisation in Grafana.
 * Random UUID → fine-grained without being PII.
 */
let sessionIdCache: string | null = null;
function getBrowserSessionId(): string {
    if (sessionIdCache) return sessionIdCache;
    try {
        sessionIdCache =
            window.crypto?.randomUUID?.() ??
            `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    } catch {
        sessionIdCache = `${Date.now().toString(36)}-x`;
    }
    return sessionIdCache;
}

export interface WebVitalPayload {
    id: string;
    name: Metric["name"];
    value: number;
    rating: Metric["rating"];
    delta: number;
    navigation_type: Metric["navigationType"];
    session_id: string;
    path: string;
    ts: number;
}

function toPayload(metric: Metric): WebVitalPayload {
    return {
        id: metric.id,
        name: metric.name,
        value: metric.value,
        rating: metric.rating,
        delta: metric.delta,
        navigation_type: metric.navigationType,
        session_id: getBrowserSessionId(),
        path: typeof window !== "undefined" ? window.location.pathname : "",
        ts: Date.now(),
    };
}

/** Send a single metric to the backend. Safe to call repeatedly. */
export function reportWebVital(metric: Metric): void {
    if (typeof window === "undefined") return;
    const url = `${API_BASE}/api/v1/metrics/web-vitals`;
    const payload = toPayload(metric);
    const body = JSON.stringify(payload);

    try {
        if (navigator.sendBeacon) {
            const ok = navigator.sendBeacon(
                url,
                new Blob([body], { type: "application/json" }),
            );
            if (ok) return;
        }
    } catch {
        // fall through to fetch fallback
    }

    // `keepalive: true` lets the request survive a nav away.
    fetch(url, {
        method: "POST",
        body,
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        credentials: "omit",
    }).catch(() => {
        /* swallow */
    });
}

// Guard so idempotent bootstrap calls don't register duplicate listeners.
let installed = false;

/**
 * Install the standard web-vitals listeners.
 *
 * Lazy-imports ``web-vitals`` so the package isn't shipped to clients
 * until the first ``reportWebVitals()`` call — keeps the initial bundle
 * tight even for anonymous / unauthenticated pages.
 */
export async function reportWebVitals(): Promise<void> {
    if (typeof window === "undefined" || installed) return;
    installed = true;
    try {
        const mod = await import("web-vitals");
        mod.onLCP(reportWebVital);
        mod.onCLS(reportWebVital);
        mod.onINP(reportWebVital);
        mod.onFCP(reportWebVital);
        mod.onTTFB(reportWebVital);
    } catch {
        // `web-vitals` not installed or failed to import — skip silently.
        installed = false;
    }
}
