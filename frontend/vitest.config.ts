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
// `e2e/` instead.
//
// The alias block mirrors the Next.js / tsconfig "@/" alias so tests
// can import the same paths the app uses.
export default defineConfig({
    plugins: [react()],
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["./vitest.setup.ts"],
        include: ["src/**/*.{test,spec}.{ts,tsx}"],
        // Server components, edge routes, and middleware never reach
        // this runner — exclude them explicitly to stop accidental
        // regressions.
        exclude: [
            "node_modules/**",
            "e2e/**",
            ".next/**",
            "src/app/**/layout.tsx",
            "src/middleware.ts",
        ],
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
