"use client";

import { SessionList } from "@/components/chat/SessionList";

/**
 * Chat shell: fixed left-rail session list + right column that hosts the
 * conversation. Both panes own their own vertical scroll — the parent
 * `<main>` (in `(app)/layout.tsx`) is `overflow-y-auto`, so we lock this
 * layer to viewport height (`h-[calc(100dvh-...)]`-style via `h-full` on a
 * `h-screen` ancestor) and clip our own overflow. That keeps the session
 * list and ChatInput pinned while the messages region scrolls
 * independently — the same pattern OpenAI / DeepSeek use.
 */
export default function ChatLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden">
      <SessionList />
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">{children}</div>
    </div>
  );
}
