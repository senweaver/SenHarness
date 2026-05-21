import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// Next.js injects several globals that JSDOM doesn't mirror; stub the
// ones our components touch so render() doesn't blow up.

// next-themes uses matchMedia for system-preference detection.
Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(),    // deprecated
        removeListener: vi.fn(), // deprecated
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
    })),
});

// IntersectionObserver is used by `@tabler/icons-react` and Radix
// popover/scroll-area. Provide a no-op implementation.
class IntersectionObserverMock {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
    takeRecords = vi.fn().mockReturnValue([]);
    root = null;
    rootMargin = "";
    thresholds = [];
}
// Use globalThis to avoid TS errors on the standard IntersectionObserver type.
(globalThis as unknown as { IntersectionObserver: unknown }).IntersectionObserver =
    IntersectionObserverMock;

// Always unmount between tests so Radix portals don't leak.
afterEach(() => {
    cleanup();
});
