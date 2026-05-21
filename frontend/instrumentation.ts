/**
 * Next.js server-side instrumentation hook.
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
 * If neither env is present the hook is a no-op — the observability plane
 * is real, it just stays quiet until operators opt in.
 */

import type * as SentryNext from "@sentry/nextjs";

type SentryModule = typeof SentryNext;
type RequestErrorArgs = Parameters<SentryModule["captureRequestError"]>;

let sentryModulePromise: Promise<SentryModule> | null = null;

function loadSentry(): Promise<SentryModule> {
  if (!sentryModulePromise) {
    sentryModulePromise = import("@sentry/nextjs");
  }
  return sentryModulePromise;
}

export async function register(): Promise<void> {
  if (process.env.NEXT_PUBLIC_SENTRY_DSN) {
    const Sentry = await loadSentry();
    const baseConfig = {
      dsn: process.env.NEXT_PUBLIC_SENTRY_DSN,
      tracesSampleRate: Number(
        process.env.NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? "0.1",
      ),
      environment:
        process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ?? process.env.NODE_ENV,
    } as const;

    if (process.env.NEXT_RUNTIME === "nodejs") {
      Sentry.init({
        ...baseConfig,
        release: process.env.NEXT_PUBLIC_APP_VERSION,
      });
    }
    if (process.env.NEXT_RUNTIME === "edge") {
      Sentry.init(baseConfig);
    }
  }

  if (
    process.env.NEXT_RUNTIME === "nodejs" &&
    process.env.OTEL_EXPORTER_OTLP_ENDPOINT
  ) {
    const { registerOTel } = await import("@vercel/otel");
    registerOTel({
      serviceName: process.env.OTEL_SERVICE_NAME ?? "senharness-frontend",
    });
  }
}

/**
 * Forwarded to Sentry when a React Server Component (or its data loader)
 * throws. `captureRequestError` was stabilised in Sentry SDK v8 and now
 * carries a matching `onRequestError` signature so we can borrow its
 * parameter list directly — no escape hatch required.
 */
export async function onRequestError(
  ...args: RequestErrorArgs
): Promise<void> {
  if (!process.env.NEXT_PUBLIC_SENTRY_DSN) return;
  const Sentry = await loadSentry();
  await Sentry.captureRequestError(...args);
}
