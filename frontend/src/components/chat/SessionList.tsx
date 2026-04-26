"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import {
  IconDots,
  IconMessageCircle,
  IconPencil,
  IconPin,
  IconPinnedOff,
  IconPlus,
  IconRobot,
  IconSearch,
  IconShare,
  IconTrash,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Link, useRouter } from "@/lib/navigation";
import { useAgents } from "@/hooks/use-agents";
import {
  useDeleteSession,
  useRecentSessions,
  useUpdateSession,
} from "@/hooks/use-sessions";
import { useSessionPinStore } from "@/stores/session-pin-store";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ShareDialog } from "@/components/chat/ShareDialog";
import { cn } from "@/lib/utils";
import type { AgentRead, SessionRead } from "@/types/api";

const DAY_MS = 24 * 60 * 60 * 1000;

type TimeBucket = "today" | "yesterday" | "last7d" | "last30d" | "older";
type DisplayBucket = "pinned" | TimeBucket;
const BUCKET_KEYS: readonly DisplayBucket[] = [
  "pinned",
  "today",
  "yesterday",
  "last7d",
  "last30d",
  "older",
];

function bucket(ts: string | null | undefined): TimeBucket {
  if (!ts) return "older";
  const then = new Date(ts).getTime();
  const now = Date.now();
  const diff = now - then;
  if (diff < DAY_MS) return "today";
  if (diff < 2 * DAY_MS) return "yesterday";
  if (diff < 7 * DAY_MS) return "last7d";
  if (diff < 30 * DAY_MS) return "last30d";
  return "older";
}

export function SessionList() {
  const { data, isLoading } = useRecentSessions(200);
  const { data: agents } = useAgents();
  const params = useParams<{ sessionId?: string }>();
  const activeId = params?.sessionId;
  const router = useRouter();

  const t = useTranslations();
  const tChat = useTranslations("chat");
  const tMenu = useTranslations("chat.menu");
  const [q, setQ] = useState("");

  const pinnedMap = useSessionPinStore((s) => s.pinned);
  const togglePin = useSessionPinStore((s) => s.toggle);
  const removePin = useSessionPinStore((s) => s.remove);
  const update = useUpdateSession();
  const remove = useDeleteSession();

  // Build a fast id → agent index so each row can render its assistant.
  const agentById = useMemo(() => {
    const m = new Map<string, AgentRead>();
    for (const a of agents ?? []) m.set(a.id, a);
    return m;
  }, [agents]);

  const filtered = useMemo(() => {
    if (!data) return [];
    if (!q.trim()) return data;
    const needle = q.toLowerCase();
    return data.filter((s) => {
      if ((s.title ?? "").toLowerCase().includes(needle)) return true;
      const agent = s.subject_id ? agentById.get(s.subject_id) : null;
      return (agent?.name ?? "").toLowerCase().includes(needle);
    });
  }, [data, q, agentById]);

  const grouped = useMemo(() => {
    const out = new Map<DisplayBucket, SessionRead[]>();
    for (const s of filtered) {
      if (pinnedMap[s.id]) {
        if (!out.has("pinned")) out.set("pinned", []);
        out.get("pinned")!.push(s);
        continue;
      }
      const b = bucket(s.last_message_at ?? s.created_at);
      if (!out.has(b)) out.set(b, []);
      out.get(b)!.push(s);
    }
    return out;
  }, [filtered, pinnedMap]);

  // ────────── rename dialog ──────────
  const [renameTarget, setRenameTarget] = useState<SessionRead | null>(null);
  const [renameTitle, setRenameTitle] = useState("");

  const openRename = (s: SessionRead) => {
    setRenameTarget(s);
    setRenameTitle(s.title ?? "");
  };

  const submitRename = async () => {
    if (!renameTarget) return;
    const title = renameTitle.trim() || null;
    try {
      await update.mutateAsync({ sessionId: renameTarget.id, title });
      toast.success(tMenu("renameSaved"));
      setRenameTarget(null);
    } catch (err) {
      toast.error((err as Error).message ?? tMenu("renameFailed"));
    }
  };

  // ────────── delete confirm ──────────
  const [deleteTarget, setDeleteTarget] = useState<SessionRead | null>(null);

  const submitDelete = async () => {
    if (!deleteTarget) return;
    const sid = deleteTarget.id;
    try {
      await remove.mutateAsync({ sessionId: sid });
      removePin(sid);
      toast.success(tMenu("deleted"));
      setDeleteTarget(null);
      // If we just deleted the open session, bounce to home so the route
      // doesn't 404 on its next REST hydration.
      if (activeId === sid) router.push("/");
    } catch (err) {
      toast.error((err as Error).message ?? tMenu("deleteFailed"));
    }
  };

  // ────────── share — controlled by sessionId pointer ──────────
  const [shareSessionId, setShareSessionId] = useState<string | null>(null);

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r sh-card">
      <div className="flex items-center justify-between border-b p-3">
        <span className="text-sm font-semibold">{t("nav.chat")}</span>
        <Button asChild size="icon" variant="ghost" className="size-7">
          <Link href="/" aria-label={t("chat.newSession")}>
            <IconPlus className="size-4" />
          </Link>
        </Button>
      </div>

      <div className="border-b p-2">
        <div className="flex items-center gap-1 rounded-md border px-2">
          <IconSearch className="size-3.5 sh-muted" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("common.search")}
            className="h-7 border-0 bg-transparent px-1 text-xs focus-visible:ring-0"
          />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2">
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
            <Skeleton className="h-7 w-full" />
          </div>
        )}

        {!isLoading && filtered.length === 0 && (
          <p className="px-2 py-4 text-xs sh-muted">
            {t("emptyStates.noSessions")}
          </p>
        )}

        {BUCKET_KEYS.map((key) => {
          const items = grouped.get(key) ?? [];
          if (items.length === 0) return null;
          const label =
            key === "pinned"
              ? tChat("pinnedBucket")
              : tChat(`buckets.${key}` as Parameters<typeof tChat>[0]);
          return (
            <div key={key} className="mb-2">
              <div className="flex items-center gap-1 px-2 py-1 text-[11px] font-medium sh-muted">
                {key === "pinned" && <IconPin className="size-3" />}
                {label}
              </div>
              {items.map((s) => {
                const agent = s.subject_id
                  ? agentById.get(s.subject_id)
                  : null;
                return (
                  <SessionRow
                    key={s.id}
                    session={s}
                    agentName={agent?.name ?? null}
                    isActive={activeId === s.id}
                    isPinned={Boolean(pinnedMap[s.id])}
                    onPin={() => togglePin(s.id)}
                    onRename={() => openRename(s)}
                    onDelete={() => setDeleteTarget(s)}
                    onShare={() => setShareSessionId(s.id)}
                  />
                );
              })}
            </div>
          );
        })}
      </div>

      {/* Rename dialog */}
      <Dialog
        open={renameTarget !== null}
        onOpenChange={(o) => {
          if (!o) setRenameTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{tMenu("renameTitle")}</DialogTitle>
          </DialogHeader>
          <Input
            value={renameTitle}
            onChange={(e) => setRenameTitle(e.target.value)}
            placeholder={tMenu("renamePlaceholder")}
            onKeyDown={(e) => {
              if (e.key === "Enter") void submitRename();
            }}
            autoFocus
          />
          <DialogFooter>
            <Button variant="ghost" onClick={() => setRenameTarget(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              onClick={() => void submitRename()}
              disabled={update.isPending}
            >
              {t("common.save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(o) => {
          if (!o) setDeleteTarget(null);
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{tMenu("delete")}</DialogTitle>
            <DialogDescription>{tMenu("deleteConfirm")}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setDeleteTarget(null)}>
              {t("common.cancel")}
            </Button>
            <Button
              variant="destructive"
              onClick={() => void submitDelete()}
              disabled={remove.isPending}
            >
              {tMenu("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Share dialog — controlled by `shareSessionId`. We mount a fresh
          instance per session id so the internal state (recipient, link)
          resets cleanly between targets. */}
      {shareSessionId !== null && (
        <ShareDialog
          key={shareSessionId}
          sessionId={shareSessionId}
          trigger={null}
          open={true}
          onOpenChange={(o) => {
            if (!o) setShareSessionId(null);
          }}
        />
      )}
    </aside>
  );
}

// ────────────────────────────────────────────
// SessionRow — extracted so each row can host its own dropdown menu
// state without re-rendering the entire SessionList on open/close.
// ────────────────────────────────────────────
interface SessionRowProps {
  session: SessionRead;
  agentName: string | null;
  isActive: boolean;
  isPinned: boolean;
  onPin: () => void;
  onRename: () => void;
  onDelete: () => void;
  onShare: () => void;
}

function SessionRow({
  session,
  agentName,
  isActive,
  isPinned,
  onPin,
  onRename,
  onDelete,
  onShare,
}: SessionRowProps) {
  const tChat = useTranslations("chat");
  const tMenu = useTranslations("chat.menu");
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div
      className={cn(
        "group relative flex items-center gap-1 rounded-md py-1.5 pl-2 pr-1 text-sm",
        isActive
          ? "bg-black/5 font-medium dark:bg-white/10"
          : "hover:bg-black/5 dark:hover:bg-white/10",
      )}
      data-testid="session-row"
    >
      <Link
        href={`/chat/${session.id}`}
        className="flex min-w-0 flex-1 items-center gap-2"
      >
        <IconMessageCircle className="size-3.5 shrink-0 sh-muted" />
        <span className="flex min-w-0 flex-1 flex-col leading-tight">
          <span className="flex items-center gap-1">
            <span className="flex-1 truncate">
              {session.title ?? tChat("untitled")}
            </span>
            {isPinned && <IconPin className="size-3 shrink-0 sh-muted" />}
          </span>
          <span className="flex items-center gap-1 truncate text-[10px] sh-muted">
            <IconRobot className="size-3 shrink-0" />
            <span className="truncate">{agentName ?? tChat("noAgent")}</span>
          </span>
        </span>
        {session.message_count > 0 && (
          <span className="text-[10px] sh-muted tabular-nums">
            {session.message_count}
          </span>
        )}
      </Link>

      <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
        <DropdownMenuTrigger asChild>
          <button
            type="button"
            aria-label="Session actions"
            className={cn(
              "flex size-6 shrink-0 items-center justify-center rounded-md",
              "opacity-0 transition-opacity group-hover:opacity-100",
              "hover:bg-black/10 dark:hover:bg-white/15",
              (menuOpen || isActive) && "opacity-100",
            )}
            onClick={(e) => e.stopPropagation()}
            data-testid="session-row-menu"
          >
            <IconDots className="size-3.5 sh-muted" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem onSelect={onRename}>
            <IconPencil className="size-3.5" />
            {tMenu("rename")}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onPin}>
            {isPinned ? (
              <>
                <IconPinnedOff className="size-3.5" />
                {tMenu("unpin")}
              </>
            ) : (
              <>
                <IconPin className="size-3.5" />
                {tMenu("pin")}
              </>
            )}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={onShare}>
            <IconShare className="size-3.5" />
            {tMenu("share")}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            onSelect={onDelete}
            className="text-red-600 focus:bg-red-500/10 focus:text-red-700 dark:text-red-400 dark:focus:text-red-300"
          >
            <IconTrash className="size-3.5" />
            {tMenu("delete")}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
