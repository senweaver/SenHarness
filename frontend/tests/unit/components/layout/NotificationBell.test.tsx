/**
 * Bell badge contract:
 *
 *   1. ``unread > 0`` paints the red badge with the number.
 *   2. ``unread === 0`` hides the badge entirely (regression: the
 *      first impl rendered an empty span that broke layout).
 *   3. ``unread > 99`` renders the "99+" sentinel.
 *
 * Higher-fidelity flows (popover open, mark-all mutation, link to
 * ``/notifications``) live in the Playwright suite — they need a real
 * react-query client + auth/workspace stores hydrated.
 */
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

// `vi.mock` hoists above all imports, so the unread count is shared via
// `vi.hoisted` rather than a top-level `let` (which would be in the
// temporal-dead-zone when the factory first runs).
const hoisted = vi.hoisted(() => ({ unread: 0, pending: 0 }));

vi.mock("@/hooks/use-notifications", () => ({
  useNotifications: () => ({ data: [], isFetching: false }),
  useUnreadNotificationCount: () => ({ data: { unread: hoisted.unread } }),
  useMarkAllNotificationsRead: () => ({
    mutate: () => undefined,
    isPending: false,
  }),
}));

vi.mock("@/hooks/use-approvals", () => ({
  useApprovalsCount: () => ({ data: { pending: hoisted.pending } }),
  useUrgentApprovals: () => ({ data: [], isFetching: false }),
}));

vi.mock("@/lib/navigation", () => ({
  Link: ({
    children,
    onClick,
  }: {
    children: React.ReactNode;
    onClick?: () => void;
    href?: string;
  }) => (
    <a href="#" onClick={onClick}>
      {children}
    </a>
  ),
  usePathname: () => "/",
}));

import { NotificationBell } from "@/components/layout/NotificationBell";

type ProviderMessages = ComponentProps<typeof NextIntlClientProvider>["messages"];

const messages = {
  notification: {
    bellTooltip: "Notifications",
    unreadBadge: "{count} unread",
    markAllRead: "Mark all read",
    noNotifications: "No notifications",
    loading: "Loading...",
    bell: {
      viewAllLink: "View all notifications",
      openPrefs: "Notification settings",
      tabNotifications: "Notifications",
      tabApprovals: "Approvals",
      emptyApprovals: "No pending approvals",
      openApprovals: "Open approvals",
    },
  },
} as unknown as ProviderMessages;

function renderBell() {
  return render(
    <NextIntlClientProvider locale="en-US" messages={messages}>
      <NotificationBell />
    </NextIntlClientProvider>,
  );
}

describe("NotificationBell badge", () => {
  it("hides the badge when there are no unread notifications", () => {
    hoisted.unread = 0;
    renderBell();
    expect(screen.queryByLabelText(/unread/)).not.toBeInTheDocument();
  });

  it("renders the count when there are unread notifications", () => {
    hoisted.unread = 7;
    renderBell();
    const badge = screen.getByLabelText("7 unread");
    expect(badge).toHaveTextContent("7");
  });

  it("renders 99+ when the count exceeds 99", () => {
    hoisted.unread = 142;
    renderBell();
    expect(screen.getByLabelText("142 unread")).toHaveTextContent("99+");
  });
});
