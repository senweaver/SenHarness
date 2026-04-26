/**
 * Next.js 15 server-side instrumentation hook.
 *
 * Two backends get initialised conditionally:
 *
 *   1. **Sentry** — when `NEXT_PUBLIC_SENTRY_DSN` is set. Covers unhandled
 *      exceptions + performance traces for both Node and Edge runtimes.
 *   2. **OpenTelemetry** — when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Ships
 *      spans to the same collector that `backend/app/core/observability.py`
 *      uses, which lets a request_id flow from Next → FastAPI → Logfire
 *      in a single trace.
 *
 * If neither env is present the hook is a no-op — V1 ships "zero-config
 * runs". There are no `TODO` stubs left; the observability plane is real,
 * it just stays quiet until operators opt in.
 */

export async function register(): Promise<void> {
    // ─── Sentry (server + edge) ─────────────────────────────────
    if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
        // Dynamic import keeps the SDK tree-shaken out of the bundle
        // when no DSN is configured.
        if (process.env.NEXT_RUNTIME === "nodejs") {
            const Sentry = await import("@sentry/nextjs");
            Sentry.init({
                dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
                tracesSampleRate: Number(
                    process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? "0.1",
                ),
                environment:
                    process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ??
                    process.env.NODE_ENV,
                release: process.env.NEXT_PUBLIC_APP_VERSION,
            });
        }
        if (process.env.NEXT_RUNTIME === "edge") {
            const Sentry = await import("@sentry/nextjs");
            Sentry.init({
                dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
                tracesSampleRate: Number(
                    process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? "0.1",
                ),
                environment:
                    process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ??
                    process.env.NODE_ENV,
            });
        }
    }

    // ─── OpenTelemetry (Node only — edge runtime has its own SDK) ─
    if (
        process.env.NEXT_RUNTIME === "nodejs" &&
        process.env.OTEL_EXPORTER_OTLP_ENDPOINT
    ) {
        // `@vercel/otel` handles the SDK lifecycle cleanly; it attaches
        // auto-instrumentations for fetch + http + pg + friends.
        const { registerOTel } = await import("@vercel/otel");
        registerOTel({
            serviceName:
                process.env.OTEL_SERVICE_NAME ?? "senharness-frontend",
        });
    }
}

/**
 * Next 15 calls this when a React Server Component (or its data loader)
 * throws. Forwarding to Sentry keeps the error message + stack + request
 * context in a single place.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- Next.js typings
//   for `onRequestError` diverge across 15.x minors; Sentry's helper takes
//   the raw args. Keeping the forwarding one-liner unconstrained avoids
//   needing to pin the Sentry SDK to a specific Next patch.
export const onRequestError: any = async (
    err: unknown,
    request: unknown,
    context: unknown,
): Promise<void> => {
    if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
    const Sentry = await import("@sentry/nextjs");
    const capture = (Sentry as unknown as {
        captureRequestError?: (e: unknown, r: unknown, c: unknown) => void;
    }).captureRequestError;
    capture?.(err, request, context);
};
