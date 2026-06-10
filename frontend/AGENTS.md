# frontend/AGENTS.md

## Stack

Next.js 16 (App Router) · React 19 · TypeScript 5.7 · shadcn/ui (Radix)
· Tailwind 4 · next-intl · next-themes · TanStack Query 5 (server state)
· Zustand 5 (client state) · @xyflow/react · Vitest · Playwright ·
**pnpm 9** (pinned in `packageManager`).

## Commands

```bash
pnpm install
cp .env.example .env.local
pnpm dev                       # http://localhost:3000

pnpm lint                      # eslint flat config
pnpm typecheck                 # tsc --noEmit
pnpm format                    # prettier write
pnpm test / pnpm test:watch    # vitest
pnpm test:e2e                  # playwright (needs backend)
pnpm build && pnpm start
```

`NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_WS_BASE_URL` are baked at
build time — rebuild after retargeting a different backend.

## Structure

```
frontend/src/
├─ app/[locale]/        App Router (locale-scoped)
│  ├─ (app)/            authenticated shell
│  ├─ (auth)/           login · register · verify-email
│  ├─ invite/[code]/
│  └─ shared/[token]/   public share page
├─ components/          feature folders + ui/ shadcn primitives
├─ hooks/               TanStack Query hooks (use-*)
├─ lib/                 api · ws · i18n · navigation · query-client · utils
├─ stores/              Zustand client-state stores
└─ types/               shared TypeScript types (api.ts)
messages/<locale>.json  next-intl bundles (en-US, zh-CN)
tests/unit/             Vitest specs, mirroring src/ layout
tests/e2e/              Playwright specs (flat) + helpers.ts + _bootstrap.ts
```

## Hard rules

1. **No inline user-facing strings.** Every label, toast, error
   message, dialog title, empty state goes through
   `messages/<locale>.json`. Active locales: `en-US`, `zh-CN`. To add
   one, copy `en-US.json` and register it in `src/lib/i18n.ts`.
2. **All HTTP goes through `src/lib/api.ts`** (bearer from
   `useAuthStore`, `X-Workspace-Id` from `useWorkspaceStore`,
   transparent 401 → `/auth/refresh` retry). Never call `fetch()`
   directly from a page or component.
3. **All WebSocket traffic goes through `src/lib/ws.ts`** — same auth
   header propagation.
4. **Agent surface label.** Render the user-visible "Agent" word
   through `src/components/nav/AgentTermLabel.tsx`, which reads
   `branding.agent_term` from workspace settings. Never hardcode
   "Agent" / "Assistant" / "助理" in JSX or i18n default values.
5. **No `any`.** TypeScript is strict; if unavoidable, follow it with
   an inline `// reason: <why>` comment.
6. **Server state vs client state.** Backend-owned data goes in a
   TanStack Query hook in `src/hooks/`. Zustand stores are only for
   ephemeral client state (auth tokens, current workspace, sidebar
   collapsed, command palette open).
7. **i18n key shape.** Stable, namespaced, dot-delimited
   (`chat.share.copyLink`, `auth.errors.invalidCredentials`). Keep
   parity across `en-US.json` and `zh-CN.json`.
8. **Test placement.** All tests live outside `src/`. Vitest specs go
   under `tests/unit/<mirror>/` matching the source path
   (`src/lib/foo.ts` → `tests/unit/lib/foo.test.ts`,
   `src/hooks/use-x.ts` → `tests/unit/hooks/use-x.test.ts`). Playwright
   specs go flat under `tests/e2e/<feature>.spec.ts` — no milestone
   subfolders, no `__tests__/` directories. Imports always use the
   `@/` alias, never `../../../src/...`. Playwright specs that only
   poke REST endpoints belong in backend pytest, not here — every
   spec in `tests/e2e/` must drive the browser (`page.*`, `seedSession`,
   or `gotoAndExpectH1`).

## Adding a feature

1. **Type** the response shape in `src/types/api.ts`.
2. **Bind** the endpoint as a typed function in `src/lib/api.ts`.
3. **Wrap** with a TanStack Query hook in `src/hooks/use-<feature>.ts`.
   Cache-key convention: `[entity, workspaceId, ...filters]`.
4. **Build** the component(s) under `src/components/<feature>/`.
   Compose shadcn primitives from `src/components/ui/`; never hand-roll
   a Radix wrapper that already exists.
5. **Add the page** under `src/app/[locale]/(app)/<feature>/...`.
   Server components by default; `'use client'` only where interactivity
   demands it.
6. **i18n keys** in both `messages/en-US.json` and `messages/zh-CN.json`.
   Keep the trees aligned.
7. **Tests.** Vitest spec under `tests/unit/<mirror>/` (see hard
   rule 8). Playwright spec under `tests/e2e/<feature>.spec.ts` —
   browser-driven only; the suite `test.skip`s itself when the
   backend isn't reachable. REST contract checks belong in backend
   pytest.
