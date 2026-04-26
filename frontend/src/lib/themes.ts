export type ThemeMode = "light" | "dark" | "system";

export const THEMES: ThemeMode[] = ["light", "dark", "system"];

export function resolveSystemTheme(): "light" | "dark" {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
