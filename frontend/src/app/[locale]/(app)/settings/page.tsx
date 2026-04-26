"use client";

import { useEffect } from "react";
import { useRouter } from "@/lib/navigation";

export default function SettingsIndex() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/settings/workspace/branding");
  }, [router]);
  return null;
}
