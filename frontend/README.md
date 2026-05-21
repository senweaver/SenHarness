# SenHarness frontend

Next.js 15 (App Router) · React 19 · shadcn/ui · Tailwind 4 ·
next-intl · TanStack Query · Zustand · @xyflow/react.

For the full product story see the root [README](../README.md). This
file covers frontend-specific development.

## Dev loop

```bash
pnpm install
cp .env.example .env.local           # pick your backend URL
pnpm dev                             # http://localhost:3000
```

`pnpm dev` hot-reloads on source changes. The API client reads
`NEXT_PUBLIC_API_BASE_URL` at build time, so a `pnpm build` is needed
if you retarget a different backend.

## Testing

```bash
pnpm test              # vitest once
pnpm test:watch        # vitest watch mode
pnpm test:e2e          # playwright (requires backend running)
pnpm test:e2e:ui       # playwright UI mode
```

Vitest covers hooks + pure utilities (`src/lib`, `src/hooks`). End-to-
end flows — login, register → create workspace, chat smoke — live
under `e2e/` and drive Playwright against a real backend.

Fresh checkouts: `pnpm test` is green without the backend running
(Vitest specs are pure JSDOM). `pnpm test:e2e` `test.skip`s itself
when the API isn't reachable so you don't see a wall of
ECONNREFUSED.

## Linting + formatting

```bash
pnpm lint              # eslint (flat config, ESLint 9)
pnpm typecheck         # tsc --noEmit
pnpm format            # prettier write
```

Pre-commit is configured at the repo root (see
`.pre-commit-config.yaml`); `pre-commit install` once per clone and
the trailing-whitespace / prettier fixes run automatically.

## Directory map

```
frontend/
├─ src/
│  ├─ app/[locale]/…          App Router pages, scoped per locale
│  ├─ components/…            UI primitives + feature components
│  ├─ hooks/…                 TanStack Query hooks + Zustand stores
│  ├─ lib/                    api client · WS client · utils · i18n
│  ├─ stores/                 Zustand global stores
│  └─ types/                  Shared TypeScript types
├─ messages/                  next-intl bundles (zh-CN · en-US · ja-JP · zh-TW · ko-KR)
├─ e2e/                       Playwright specs
├─ public/                    Static assets (logos, favicons)
├─ vitest.config.ts           Vitest config
├─ playwright.config.ts       Playwright config
├─ next.config.ts             Next.js config
├─ instrumentation.ts         Observability hook (stub — see docs/deployment.md)
└─ Dockerfile                 multi-stage (deps → dev → builder → runner)
```

## Build

```bash
pnpm build                           # production build
NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
NEXT_PUBLIC_WS_BASE_URL=wss://api.example.com \
    pnpm build                       # with deploy URLs baked in
pnpm start                           # serve the standalone build
```

Docker builds go through the four-stage Dockerfile:

```bash
docker build -t senharness/frontend \
    --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.com \
    --build-arg NEXT_PUBLIC_WS_BASE_URL=wss://api.example.com \
    .
```

## Observability

`instrumentation.ts` is the single place to wire a provider:

- Sentry — drop `@sentry/nextjs` and call `Sentry.init`.
- OTel — `@vercel/otel` for most deployments.
- Logfire — client-side tracing sent to the same project as the
  backend.

Stubbed in V1 to avoid hard-coding a dependency; enable when you
decide which provider you're standardising on.

## Internationalisation

Translations live under `messages/<locale>.json`. Add a new locale by
copying `en-US.json`, editing keys, and registering the locale in
`src/lib/i18n.ts`. At build time `next-intl` embeds the active locale
in the URL (`/en-US/…`, `/zh-CN/…`).

## Troubleshooting

**Blank page on first load**
The most common cause is `NEXT_PUBLIC_API_BASE_URL` baked with the
wrong value. Check the browser console — the failing request URL tells
you what got baked. Rebuild with the right env.

**Hydration warnings about theme**
`next-themes` suppresses the first-render mismatch by design. If you
see a real hydration warning from something else, check
`suppressHydrationWarning` usage in `app/[locale]/layout.tsx`.

**Vitest fails with `ReferenceError: matchMedia is not defined`**
Make sure `vitest.setup.ts` is being loaded — the config points at it
via `setupFiles`. Adding your own setup? Extend the existing file
rather than overriding the config.
