"use client";

import { useEffect } from "react";

export function NextThemesScriptTagFilter() {
  useEffect(() => {
    const orig = console.error;
    console.error = (...args: unknown[]) => {
      const first = args[0];
      if (
        typeof first === "string" &&
        first.includes("Encountered a script tag")
      ) {
        return;
      }
      orig(...(args as []));
    };
    return () => {
      console.error = orig;
    };
  }, []);
  return null;
}
