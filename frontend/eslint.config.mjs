import coreWebVitals from "eslint-config-next/core-web-vitals";
import typescript from "eslint-config-next/typescript";

const eslintConfig = [
  ...coreWebVitals,
  ...typescript,
  {
    rules: {
      // The React 19 hook plugin shipped with eslint-config-next 16 newly
      // flags several patterns we still rely on across chat / agent /
      // admin pages: syncing state from props, mutating refs in render,
      // and manual memoisation. Each deserves its own refactor (derived
      // state, effect-driven ref writes, React Compiler-friendly memoisation)
      // but doing that here would balloon the upgrade beyond Next-version
      // scope. Surface as warnings so the cleanup is owned, don't gate
      // CI on them yet.
      "react-hooks/set-state-in-effect": "warn",
      "react-hooks/set-state-in-render": "warn",
      "react-hooks/refs": "warn",
      "react-hooks/preserve-manual-memoization": "warn",
      "react-hooks/static-components": "warn",
      "react-hooks/purity": "warn",
      "react-hooks/immutability": "warn",
    },
  },
  {
    ignores: [
      ".next/**",
      "out/**",
      "build/**",
      "node_modules/**",
      "next-env.d.ts",
      "playwright-report/**",
      "test-results/**",
      "coverage/**",
    ],
  },
];

export default eslintConfig;
