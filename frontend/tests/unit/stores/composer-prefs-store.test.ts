import { beforeEach, describe, expect, it } from "vitest";

import { useComposerPrefsStore } from "@/stores/composer-prefs-store";

describe("composer-prefs-store", () => {
  beforeEach(() => {
    useComposerPrefsStore.setState({ mode: "flash" });
  });

  it("defaults to flash", () => {
    expect(useComposerPrefsStore.getState().mode).toBe("flash");
  });

  it("persists the picked mode via setMode", () => {
    useComposerPrefsStore.getState().setMode("thinking");
    expect(useComposerPrefsStore.getState().mode).toBe("thinking");
  });
});
