"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { SiderNav } from "@/components/layout/SiderNav";
import { CommandPalette } from "@/components/layout/CommandPalette";
import { OnboardingOverlay } from "@/components/onboarding/OnboardingOverlay";
import { WorkspaceRequiredGuard } from "@/components/workspace/WorkspaceRequiredGuard";
import { useAuthStore } from "@/stores/auth-store";
import { useAutoCollapseOnChat } from "@/hooks/use-auto-collapse-on-chat";
import { useSyncWorkspaceBranding } from "@/hooks/use-workspace";
import { useMe } from "@/hooks/use-me";
import { reportWebVitals } from "@/lib/web-vitals";

const SLUG_WARNING_KEY = "senharness:slug_warning";

export default function AppShellLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const token = useAuthStore((s) => s.accessToken);
  const t = useTranslations("register");

  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (mounted && !token) router.replace("/login");
  }, [mounted, token, router]);

  useEffect(() => {
    void reportWebVitals();
  }, []);

  // Surface the personal-workspace random-suffix warning exactly once
  // after register; the register page sets the sessionStorage payload
  // when the slug allocator fell to the 6-hex tail.
  useEffect(() => {
    if (!mounted || typeof window === "undefined") return;
    try {
      const raw = sessionStorage.getItem(SLUG_WARNING_KEY);
      if (!raw) return;
      sessionStorage.removeItem(SLUG_WARNING_KEY);
      const parsed = JSON.parse(raw) as { slug?: string };
      if (parsed?.slug) {
        toast.info(t("slugWarningTitle"), {
          description: t("slugWarningBody", { slug: parsed.slug }),
        });
      }
    } catch {
      // sessionStorage / JSON disabled — silent skip
    }
  }, [mounted, t]);

  useMe();
  useSyncWorkspaceBranding();
  useAutoCollapseOnChat();

  if (!mounted || !token) return null;

  return (
    <div className="flex h-screen overflow-hidden">
      <SiderNav />
      <main className="flex h-full min-h-0 flex-1 flex-col overflow-y-auto">
        <WorkspaceRequiredGuard>{children}</WorkspaceRequiredGuard>
      </main>
      <CommandPalette />
      <OnboardingOverlay />
    </div>
  );
}
