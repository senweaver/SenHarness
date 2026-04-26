"use client";

import { useRouter } from "@/lib/navigation";
import { useEffect, useState } from "react";
import { SiderNav } from "@/components/layout/SiderNav";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { OnboardingTour } from "@/components/onboarding/OnboardingTour";
import { useAuthStore } from "@/stores/auth-store";
import { useSyncWorkspaceBranding } from "@/hooks/use-workspace";
import { useMe } from "@/hooks/use-me";
import { reportWebVitals } from "@/lib/web-vitals";

export default function AppShellLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const token = useAuthStore((s) => s.accessToken);
  // Defer auth check until after client-side hydration so Zustand's persist
  // middleware has had a chance to read from localStorage. Without this guard
  // the layout sometimes redirects to /login before the persisted token is loaded.
  const [mounted, setMounted] = useState(false);
  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (mounted && !token) router.replace("/login");
  }, [mounted, token, router]);

  // Browser telemetry — fire once per SPA session. `reportWebVitals`
  // is a no-op if `web-vitals` isn't installed or if `window` isn't
  // defined.
  useEffect(() => {
    void reportWebVitals();
  }, []);

  // Prime identity + active workspace; sync branding into store continuously.
  useMe();
  useSyncWorkspaceBranding();

  if (!mounted || !token) return null;

  return (
    <div className="flex h-screen overflow-hidden">
      <SiderNav />
      {/*
       * `min-h-0` is critical: without it, flex children default to
       * `min-height: auto` which prevents them from shrinking below their
       * content size. Pages like /chat rely on `h-full + overflow-hidden`
       * to keep their message list and input pinned independently — that
       * only works if the parent <main> can be told to shrink.
       */}
      <main className="flex h-full min-h-0 flex-1 flex-col overflow-y-auto">{children}</main>
      <CommandPalette />
      <OnboardingTour />
    </div>
  );
}
