"use client";

import { useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconBuildingCommunity,
  IconBellFilled,
  IconHourglass,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  useApprovalsCount,
  useUrgentApprovals,
} from "@/hooks/use-approvals";
import { useCountdown } from "@/hooks/use-countdown";
import { cn } from "@/lib/utils";
import type { ApprovalRead } from "@/types/api";

/**
 * `ApprovalsBell` — compact bell icon intended for the sidebar footer /
 * workspace chrome. Click opens a popover with the 5 most-urgent pending
 * approvals (earliest expiry first). Each row links to `/approvals` so the
 * user can decide in the full queue view.
 *
 * The badge is driven by ``useApprovalsCount`` (same poll as AvatarMenu so
 * both badges stay in sync). The list comes from the dedicated
 * ``/approvals/urgent`` endpoint to avoid dragging down the main list query.
 */
export function ApprovalsBell({ className }: { className?: string }) {
  const t = useTranslations("approvals");
  const tBell = useTranslations("approvals.bell");
  const [open, setOpen] = useState(false);
  const { data: count } = useApprovalsCount();
  const pending = count?.pending ?? 0;
  // Only fetch the list while the popover is open; stale data on next open is
  // fine because the endpoint ordering is cheap and the cache's 30s interval
  // still ticks in the background.
  const { data: urgent, isFetching } = useUrgentApprovals({
    limit: 5,
    enabled: open,
  });

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          "relative flex size-8 items-center justify-center rounded-md hover:bg-black/5 dark:hover:bg-white/10",
          className,
        )}
        aria-label={tBell("ariaLabel")}
      >
        <IconBellFilled className="size-4" />
        {pending > 0 && (
          <span className="absolute -top-0.5 -right-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-red-500 px-1 text-[9px] font-bold text-white">
            {pending > 99 ? "99+" : pending}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent align="start" side="top" className="w-80 p-2">
        <div className="mb-2 flex items-center justify-between px-1 text-xs">
          <span className="font-medium">{tBell("title")}</span>
          <Link
            href="/approvals"
            onClick={() => setOpen(false)}
            className="text-[11px] text-[rgb(var(--color-primary))] hover:underline"
          >
            {tBell("viewAll")}
          </Link>
        </div>
        {isFetching && !urgent ? (
          <p className="py-4 text-center text-[11px] sh-muted">
            {tBell("loading")}
          </p>
        ) : !urgent || urgent.length === 0 ? (
          <p className="py-4 text-center text-[11px] sh-muted">
            {tBell("empty")}
          </p>
        ) : (
          <ul className="space-y-1">
            {urgent.map((row) => (
              <BellRow
                key={row.id}
                row={row}
                onClick={() => setOpen(false)}
              />
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}

function BellRow({
  row,
  onClick,
}: {
  row: ApprovalRead;
  onClick: () => void;
}) {
  const t = useTranslations("approvals");
  const { label, totalMs, expired } = useCountdown(row.expires_at);
  const urgency: "red" | "amber" | "neutral" = !row.expires_at
    ? "neutral"
    : expired || totalMs <= 60_000
      ? "red"
      : totalMs <= 120_000
        ? "amber"
        : "neutral";
  const urgencyCn = cn(
    "flex items-center gap-1 font-mono tabular-nums text-[10px]",
    urgency === "red" && "text-rose-600 dark:text-rose-400",
    urgency === "amber" && "text-amber-600 dark:text-amber-400",
    urgency === "neutral" && "sh-muted",
  );
  return (
    <li>
      <Link
        href="/approvals"
        onClick={onClick}
        className="flex flex-col gap-0.5 rounded px-2 py-1.5 text-xs hover:bg-black/5 dark:hover:bg-white/5"
      >
        <div className="flex items-center gap-2">
          <span className="truncate font-mono">{row.tool_name}</span>
          {row.expires_at && (
            <span className={cn(urgencyCn, "ml-auto")}>
              <IconHourglass className="size-3" />
              {expired ? t("expiredLabel") : label}
            </span>
          )}
        </div>
        {row.summary && (
          <div className="line-clamp-1 font-mono text-[10.5px] sh-muted">
            {row.summary}
          </div>
        )}
        {row.requester_department_name && (
          <div className="flex items-center gap-1 text-[10px] sh-muted">
            <IconBuildingCommunity className="size-3" />
            {row.requester_department_name}
          </div>
        )}
      </Link>
    </li>
  );
}
