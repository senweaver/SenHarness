"use client";

import { useState } from "react";
import {
  IconCopy,
  IconLink,
  IconLoader2,
  IconShare,
  IconTrash,
  IconUserPlus,
} from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateShare,
  useRevokeShare,
  useSessionShares,
} from "@/hooks/use-session-shares";
import { cn } from "@/lib/utils";
import type { SessionShareRead, SharePermission } from "@/types/api";

interface ShareDialogProps {
  sessionId: string;
  /** Render-prop trigger; defaults to a small icon-only "share" button.
   *  Pass `null` to fully hide the trigger and drive the dialog purely
   *  via the controlled `open` prop. */
  trigger?: React.ReactNode | null;
  /** Optional controlled visibility — pair with `onOpenChange` to drive
   *  the dialog from a parent (e.g. a session row dropdown). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  className?: string;
}

/**
 * `ShareDialog` — invite users by email, generate a public link, list +
 * revoke existing shares.
 *
 * Three independent flows live in the same modal:
 *   1. Email/UUID + permission select + invite → `POST /shares` with
 *      ``shared_with`` set.
 *   2. "Generate link" button → `POST /shares` with ``generate_link=true``;
 *      result row carries ``token`` so we render the public URL inline.
 *   3. Existing shares list with revoke (trash icon).
 */
export function ShareDialog({
  sessionId,
  trigger,
  open: openProp,
  onOpenChange,
  className,
}: ShareDialogProps) {
  const t = useTranslations("chat.share");
  const [openState, setOpenState] = useState(false);
  const isControlled = openProp !== undefined;
  const open = isControlled ? openProp : openState;
  const setOpen = (next: boolean) => {
    if (!isControlled) setOpenState(next);
    onOpenChange?.(next);
  };
  const [recipient, setRecipient] = useState("");
  const [permission, setPermission] = useState<SharePermission>("view");
  const [generatedLink, setGeneratedLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const sharesQ = useSessionShares(open ? sessionId : null);
  const create = useCreateShare();
  const revoke = useRevokeShare();
  const items = sharesQ.data?.items ?? [];

  const handleInvite = async () => {
    const target = recipient.trim();
    if (!target) {
      toast.error(t("missingRecipient"));
      return;
    }
    try {
      await create.mutateAsync({
        sessionId,
        shared_with: target,
        permission,
      });
      setRecipient("");
      toast.success(t("invited"));
    } catch (err) {
      toast.error((err as Error).message ?? t("inviteFailed"));
    }
  };

  const handleGenerateLink = async () => {
    try {
      const share = await create.mutateAsync({
        sessionId,
        generate_link: true,
        permission,
      });
      if (share.token) {
        const url = `${window.location.origin}/shared/${share.token}`;
        setGeneratedLink(url);
        toast.success(t("linkGenerated"));
      }
    } catch (err) {
      toast.error((err as Error).message ?? t("linkFailed"));
    }
  };

  const handleCopyLink = async () => {
    if (!generatedLink) return;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(generatedLink);
      } else {
        const ta = document.createElement("textarea");
        ta.value = generatedLink;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error(t("copyFailed"));
    }
  };

  const handleRevoke = async (share: SessionShareRead) => {
    try {
      await revoke.mutateAsync({ sessionId, shareId: share.id });
      toast.success(t("revoked"));
    } catch (err) {
      toast.error((err as Error).message ?? t("revokeFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => {
      setOpen(o);
      if (!o) {
        setGeneratedLink(null);
        setCopied(false);
      }
    }}>
      {trigger !== null && (
        <DialogTrigger asChild>
          {trigger ?? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              aria-label={t("title")}
              title={t("title")}
              className={cn("h-7 w-7", className)}
              data-testid="share-trigger"
            >
              <IconShare className="size-3.5" />
            </Button>
          )}
        </DialogTrigger>
      )}
      <DialogContent className="sm:max-w-md" data-testid="share-dialog">
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* 1) Direct invite */}
          <div className="flex gap-2">
            <Input
              placeholder={t("recipientPlaceholder")}
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleInvite();
              }}
              data-testid="share-recipient"
            />
            <Select
              value={permission}
              onValueChange={(v) => setPermission(v as SharePermission)}
            >
              <SelectTrigger className="w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="view">{t("permView")}</SelectItem>
                <SelectItem value="edit">{t("permEdit")}</SelectItem>
              </SelectContent>
            </Select>
            <Button
              onClick={handleInvite}
              disabled={create.isPending || !recipient.trim()}
              size="icon"
              aria-label={t("invite")}
              title={t("invite")}
              data-testid="share-invite"
            >
              {create.isPending ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconUserPlus className="size-4" />
              )}
            </Button>
          </div>

          {/* 2) Public link */}
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={handleGenerateLink}
              disabled={create.isPending}
              className="flex-1"
              data-testid="share-generate-link"
            >
              <IconLink className="size-4" />
              {t("generateLink")}
            </Button>
            {generatedLink && (
              <Button
                variant="subtle"
                size="icon"
                onClick={handleCopyLink}
                aria-label={copied ? t("copied") : t("copy")}
                title={copied ? t("copied") : t("copy")}
              >
                <IconCopy className="size-4" />
              </Button>
            )}
          </div>
          {generatedLink && (
            <p className="break-all text-[11px] sh-muted">
              {copied ? t("copied") : generatedLink}
            </p>
          )}

          {/* 3) Existing shares */}
          {items.length > 0 && (
            <div className="space-y-1.5 border-t pt-3">
              <p className="text-xs font-medium">{t("listHeading")}</p>
              {items.map((share) => (
                <div
                  key={share.id}
                  className="flex items-center justify-between rounded-md border p-2"
                  data-testid="share-row"
                >
                  <div className="min-w-0 flex flex-1 items-center gap-2">
                    <span className="truncate text-xs">
                      {share.shared_with_email ??
                        (share.token ? t("publicLinkLabel") : "—")}
                    </span>
                    <Badge variant="outline">
                      {share.permission === "edit" ? t("permEdit") : t("permView")}
                    </Badge>
                    {share.token && <Badge variant="primary">{t("linkBadge")}</Badge>}
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleRevoke(share)}
                    disabled={revoke.isPending}
                    aria-label={t("revoke")}
                    title={t("revoke")}
                    className="h-7 w-7"
                    data-testid="share-revoke"
                  >
                    <IconTrash className="size-3.5" />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
