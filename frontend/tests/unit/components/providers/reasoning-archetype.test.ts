import { describe, expect, it } from "vitest";

import {
  archetypeOf,
  reasoningFromArchetype,
} from "@/components/providers/reasoning-archetype";

describe("reasoning-archetype mapping", () => {
  it("derives the archetype from supported/hybrid flags", () => {
    expect(archetypeOf(false, false)).toBe("none");
    expect(archetypeOf(false, true)).toBe("none");
    expect(archetypeOf(true, false)).toBe("always");
    expect(archetypeOf(true, true)).toBe("hybrid");
  });

  it("round-trips archetype back to supported/hybrid", () => {
    expect(reasoningFromArchetype("none")).toEqual({
      supported: false,
      hybrid: false,
    });
    expect(reasoningFromArchetype("always")).toEqual({
      supported: true,
      hybrid: false,
    });
    expect(reasoningFromArchetype("hybrid")).toEqual({
      supported: true,
      hybrid: true,
    });
  });

  it("is stable across the round trip for valid combinations", () => {
    for (const [supported, hybrid] of [
      [false, false],
      [true, false],
      [true, true],
    ] as const) {
      const archetype = archetypeOf(supported, hybrid);
      expect(reasoningFromArchetype(archetype)).toEqual({ supported, hybrid });
    }
  });
});
