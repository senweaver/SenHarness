import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import path from "node:path";

// V1 frontend unit-test config.
//
// Scope is deliberately narrow: hooks, pure lib functions, and small
// presentational components that can render in JSDOM without hitting a
// Next.js server. Anything that needs the full Next.js runtime
// (middleware, server components, layout) belongs in Playwright under
// `tests/e2e/` instead.
//
// Test files live under `tests/unit/` mirroring `src/` (see
// `frontend/AGENTS.md`). Imports go through the `@/` alias so tests
// stay stable when source files move within `src/`.
export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["./vitest.setup.ts"],
        include: ["tests/unit/**/*.{test,spec}.{ts,tsx}"],
        exclude: ["node_modules/**", "tests/e2e/**", ".next/**"],
        coverage: {
            provider: "v8",
            reporter: ["text", "html"],
            include: ["src/components/**", "src/hooks/**", "src/lib/**"],
            exclude: ["**/*.d.ts", "src/**/index.ts"],
        },
    },
    resolve: {
        alias: {
            "@": path.resolve(
                path.dirname(fileURLToPath(import.meta.url)),
                "./src",
            ),
        },
    },
});
