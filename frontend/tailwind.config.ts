import type { Config } from "tailwindcss";

// Tailwind v4 uses CSS-based config; this file is only for editor IntelliSense.
const config: Config = {
  content: [
    "./src/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;
