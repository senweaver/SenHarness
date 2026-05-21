"use client";

import { useEffect, useRef } from "react";

import { usePathname } from "@/lib/navigation";
import { useSidebarStore } from "@/stores/sidebar-store";

const CHAT_PATH = /^\/(?:[a-z]{2}-[A-Z]{2}\/)?chat(?:\/|$)/;

function isChat(path: string | null): boolean {
  return path !== null && CHAT_PATH.test(path);
}

/**
 * One-shot collapse of the main SiderNav when the user enters `/chat/*`
 * from a non-chat route. On exit, restore whatever the rail was set to
 * when we collapsed it. Sub-route transitions inside `/chat` do not
 * touch the rail — manual toggles inside chat win.
 */
export function useAutoCollapseOnChat() {
  const pathname = usePathname();
  const collapsed = useSidebarStore((s) => s.collapsed);
  const setCollapsed = useSidebarStore((s) => s.setCollapsed);
  const preChatCollapsed = useSidebarStore((s) => s.preChatCollapsed);
  const setPreChatCollapsed = useSidebarStore((s) => s.setPreChatCollapsed);
  const prevPathRef = useRef<string | null>(null);

  useEffect(() => {
    const prev = prevPathRef.current;
    const curr = pathname;
    const prevIsChat = isChat(prev);
    const currIsChat = isChat(curr);

    if (currIsChat && !prevIsChat) {
      setPreChatCollapsed(collapsed);
      if (!collapsed) setCollapsed(true);
    } else if (!currIsChat && prevIsChat) {
      if (preChatCollapsed !== null) {
        setCollapsed(preChatCollapsed);
        setPreChatCollapsed(null);
      }
    }

    prevPathRef.current = curr;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);
}
