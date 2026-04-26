"use client";

import { useEffect } from "react";
import { useRouter } from "@/lib/navigation";

/** Redirect shim — the avatar menu links here but the real landing page is
 * the branding tab. Keeping this route avoids 404s when the workspace name
 * shows up in user bookmarks or old emails. */
export default function WorkspaceGeneralRedirect() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/settings/workspace/branding");
  }, [router]);
  return null;
}
