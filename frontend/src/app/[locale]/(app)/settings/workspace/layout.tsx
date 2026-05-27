import type { ReactNode } from "react";

export default function WorkspaceSettingsRouteLayout({
  children,
}: {
  children: ReactNode;
}) {
  // The outer (app) layout now wraps all authenticated routes with
  // ``WorkspaceRequiredGuard`` — no extra gate needed here.
  return <>{children}</>;
}
