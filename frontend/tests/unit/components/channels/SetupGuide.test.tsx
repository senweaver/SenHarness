/**
 * Tests for SetupGuide. The component encapsulates two pieces of UX
 * contract that are easy to break in a refactor:
 *
 *   1. First visit auto-expands the steps; collapsing once writes ``1``
 *      to localStorage so subsequent visits stay collapsed.
 *   2. Unknown kinds render nothing rather than an empty placeholder.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ComponentProps } from "react";

import { SetupGuide } from "@/components/channels/SetupGuide";

type ProviderMessages = ComponentProps<typeof NextIntlClientProvider>["messages"];

// next-intl's `Messages` type is recursively string-or-object only — it
// doesn't admit arrays at the type level even though `t.raw()` reads
// them at runtime. The cast keeps the test honest while still letting
// us assert the array-of-steps rendering path.
const messages = {
  settings: {
    channels: {
      guide: {
        expand: "View setup steps",
        collapse: "Collapse steps",
        dismiss: "Got it",
        wechat: {
          intro: "Bind a personal WeChat by scanning a QR.",
          steps: [
            "Save the channel",
            "Click 'Scan to log in' on the channel card",
            "Scan with the WeChat you want to bind",
          ],
        },
      },
    },
  },
} as unknown as ProviderMessages;

function renderGuide(kind: string) {
  return render(
    <NextIntlClientProvider locale="en-US" messages={messages}>
      <SetupGuide kind={kind} />
    </NextIntlClientProvider>,
  );
}

const STORAGE_KEY = "senharness:channelGuide:dismissed:wechat";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("SetupGuide", () => {
  it("renders nothing when the kind has no i18n entry", () => {
    const { container } = renderGuide("definitely-not-a-real-kind");
    expect(container.firstChild).toBeNull();
  });

  it("auto-expands on first visit (no localStorage flag)", async () => {
    renderGuide("wechat");
    // ``steps`` only renders when the panel is open — using the first
    // step's text as the open/closed indicator avoids confusing the
    // collapsed-state intro preview with the expanded body.
    expect(await screen.findByText("Save the channel")).toBeInTheDocument();
  });

  it("starts collapsed when the dismissed flag is already set", () => {
    window.localStorage.setItem(STORAGE_KEY, "1");
    renderGuide("wechat");
    expect(screen.queryByText("Save the channel")).not.toBeInTheDocument();
    expect(screen.getByRole("button")).toBeInTheDocument();
  });

  it("persists the collapsed state when the operator clicks the header", async () => {
    renderGuide("wechat");
    expect(await screen.findByText("Save the channel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("1");
    expect(screen.queryByText("Save the channel")).not.toBeInTheDocument();
  });

  it("toggles the flag back to 0 when the operator re-expands", () => {
    window.localStorage.setItem(STORAGE_KEY, "1");
    renderGuide("wechat");
    fireEvent.click(screen.getByRole("button"));
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("0");
  });
});
