/**
 * Workspace switcher status row (variant D2) contract:
 *
 *   1. When ``summary`` is null or every counter is zero, render the
 *      ``idle`` label so the row keeps its two-line height.
 *   2. Each non-zero counter renders a colored dot + its number.
 *   3. The container exposes an aria-label that includes the workspace
 *      name and a human-readable counter list for screen readers.
 */
import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ComponentProps } from "react";
import { describe, expect, it } from "vitest";

import { WorkspaceStatusRow } from "@/components/layout/WorkspaceStatusRow";

type ProviderMessages = ComponentProps<typeof NextIntlClientProvider>["messages"];

const messages = {
  workspaceSwitcher: {
    status: {
      running: "{count} running",
      stuck: "{count} stuck",
      orphan: "{count} orphan",
      idle: "idle",
      statusForWorkspace: "Status for {name}: {summary}",
    },
  },
} as unknown as ProviderMessages;

function renderRow(props: Parameters<typeof WorkspaceStatusRow>[0]) {
  return render(
    <NextIntlClientProvider locale="en-US" messages={messages}>
      <WorkspaceStatusRow {...props} />
    </NextIntlClientProvider>,
  );
}

describe("WorkspaceStatusRow", () => {
  it("renders 'idle' when the summary is null", () => {
    renderRow({ workspaceName: "Alpha", summary: null });
    expect(screen.getByText("idle")).toBeInTheDocument();
    expect(
      screen.queryByTestId("workspace-status-running"),
    ).not.toBeInTheDocument();
  });

  it("renders 'idle' when every counter is zero", () => {
    renderRow({
      workspaceName: "Alpha",
      summary: {
        workspace_id: "ws-1",
        running: 0,
        stuck: 0,
        orphan: 0,
        queued: 0,
      },
    });
    expect(screen.getByText("idle")).toBeInTheDocument();
  });

  it("renders only the non-zero counters", () => {
    renderRow({
      workspaceName: "Alpha",
      summary: {
        workspace_id: "ws-1",
        running: 2,
        stuck: 0,
        orphan: 1,
        queued: 0,
      },
    });
    const running = screen.getByTestId("workspace-status-running");
    expect(within(running).getByText("2")).toBeInTheDocument();
    const orphan = screen.getByTestId("workspace-status-orphan");
    expect(within(orphan).getByText("1")).toBeInTheDocument();
    expect(
      screen.queryByTestId("workspace-status-stuck"),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("idle")).not.toBeInTheDocument();
  });

  it("exposes an aria-label summarising the counters", () => {
    renderRow({
      workspaceName: "Alpha",
      summary: {
        workspace_id: "ws-1",
        running: 3,
        stuck: 1,
        orphan: 0,
        queued: 0,
      },
    });
    const status = screen.getByTestId("workspace-status-row");
    expect(status).toHaveAttribute(
      "aria-label",
      "Status for Alpha: 3 running, 1 stuck",
    );
  });
});
