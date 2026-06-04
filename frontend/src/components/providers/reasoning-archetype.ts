/**
 * Thinking-type archetype ↔ reasoning flags mapping.
 *
 * A model's reasoning behaviour is exposed in the dialog as a single
 * archetype rather than two free toggles. The runner still reads the
 * underlying ``supported`` / ``hybrid`` flags, so this is the one place
 * the two representations convert.
 */
export type Archetype = "none" | "always" | "hybrid";

export function archetypeOf(supported: boolean, hybrid: boolean): Archetype {
  if (!supported) return "none";
  return hybrid ? "hybrid" : "always";
}

export function reasoningFromArchetype(archetype: Archetype): {
  supported: boolean;
  hybrid: boolean;
} {
  return {
    supported: archetype !== "none",
    hybrid: archetype === "hybrid",
  };
}
