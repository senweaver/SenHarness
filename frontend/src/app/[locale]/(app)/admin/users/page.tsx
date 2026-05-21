"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/navigation";
import {
  IconBrain,
  IconCheck,
  IconLoader2,
  IconSearch,
  IconShield,
  IconShieldOff,
  IconUserPause,
  IconUserPlus,
  IconUsers,
} from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { PageHeader } from "@/components/ui/page-header";
import {
  type IdentityAdminRow,
  type IdentityStatus,
  type PlatformRoleT,
  useAdminIdentities,
  useAdminIdentity,
  usePatchIdentity,
} from "@/hooks/use-admin";
import { useMe } from "@/hooks/use-me";
import { relativeTime } from "@/lib/utils";

export default function AdminUsersPage() {
  const t = useTranslations("admin.users");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [statusFilter, setStatusFilter] = useState<IdentityStatus | "">("");
  const [roleFilter, setRoleFilter] = useState<PlatformRoleT | "">("");
  const [focusId, setFocusId] = useState<string | null>(null);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(q), 300);
    return () => clearTimeout(id);
  }, [q]);

  const { data, isLoading } = useAdminIdentities({
    q: debouncedQ || undefined,
    status: statusFilter || undefined,
    role: roleFilter || undefined,
  });

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <span className="text-[11px] sh-muted">
            {t("showing", { n: (data ?? []).length })}
          </span>
        }
      />

      <Card className="mb-3">
        <CardContent className="grid gap-3 py-3 sm:grid-cols-[1fr_160px_180px]">
          <div className="relative">
            <IconSearch className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 sh-muted" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={t("searchPlaceholder")}
              className="pl-7"
            />
          </div>
          <Select
            value={statusFilter || "all"}
            onValueChange={(v) =>
              setStatusFilter(v === "all" ? "" : (v as IdentityStatus))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filter.anyStatus")}</SelectItem>
              <SelectItem value="active">{t("status.active")}</SelectItem>
              <SelectItem value="pending">{t("status.pending")}</SelectItem>
              <SelectItem value="suspended">{t("status.suspended")}</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={roleFilter || "all"}
            onValueChange={(v) =>
              setRoleFilter(v === "all" ? "" : (v as PlatformRoleT))
            }
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filter.anyRole")}</SelectItem>
              <SelectItem value="platform_admin">
                {t("role.platform_admin")}
              </SelectItem>
              <SelectItem value="user">{t("role.user")}</SelectItem>
            </SelectContent>
          </Select>
        </CardContent>
      </Card>

      {isLoading && <Skeleton className="h-60" />}

      {!isLoading && (data ?? []).length === 0 && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            <IconUsers className="mx-auto size-8" />
            <p className="mt-2">{t("empty")}</p>
          </CardContent>
        </Card>
      )}

      <div className="space-y-1.5">
        {(data ?? []).map((row) => (
          <IdentityRow key={row.id} row={row} onOpen={() => setFocusId(row.id)} />
        ))}
      </div>

      <IdentityDialog
        identityId={focusId}
        open={Boolean(focusId)}
        onOpenChange={(v) => !v && setFocusId(null)}
      />
    </div>
  );
}

function IdentityRow({
  row,
  onOpen,
}: {
  row: IdentityAdminRow;
  onOpen: () => void;
}) {
  const t = useTranslations("admin.users");
  const locale = useLocale();
  const initial = (row.name || row.email).slice(0, 1).toUpperCase();
  return (
    <Card
      className="cursor-pointer transition-colors hover:bg-black/3 dark:hover:bg-white/3"
      onClick={onOpen}
    >
      <CardContent className="flex items-center gap-3 py-2">
        <Avatar className="size-9 shrink-0">
          {row.avatar_url && <AvatarImage src={row.avatar_url} alt={row.name} />}
          <AvatarFallback>{initial}</AvatarFallback>
        </Avatar>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium">{row.name}</span>
            {row.platform_role === "platform_admin" && (
              <Badge variant="warning" className="gap-0.5">
                <IconShield className="size-2.5" />
                admin
              </Badge>
            )}
            {row.oauth_provider && (
              <Badge variant="outline">{row.oauth_provider}</Badge>
            )}
            <Badge
              variant={
                row.status === "active"
                  ? "success"
                  : row.status === "suspended"
                    ? "danger"
                    : "outline"
              }
            >
              {t(`status.${row.status}`)}
            </Badge>
          </div>
          <div className="truncate text-[11px] sh-muted">{row.email}</div>
        </div>
        <span className="hidden text-[11px] sh-muted sm:inline-block">
          {t("workspaces", { n: row.workspace_count })}
        </span>
        <span className="text-[11px] sh-muted tabular-nums">
          {relativeTime(row.created_at, locale)}
        </span>
      </CardContent>
    </Card>
  );
}

function IdentityDialog({
  identityId,
  open,
  onOpenChange,
}: {
  identityId: string | null;
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const t = useTranslations("admin.users");
  const tProfile = useTranslations("admin.identityProfile");
  const { data: me } = useMe();
  const { data: detail, isLoading } = useAdminIdentity(
    open ? identityId : null,
  );
  const patch = usePatchIdentity();

  const isSelf = detail?.id === me?.id;

  const toggleStatus = async (target: IdentityStatus) => {
    if (!detail) return;
    try {
      await patch.mutateAsync({ id: detail.id, status: target });
      toast.success(t(target === "suspended" ? "suspended" : "activated"));
    } catch {
      toast.error(t("patchFailed"));
    }
  };

  const toggleAdmin = async (newRole: PlatformRoleT) => {
    if (!detail) return;
    if (
      newRole === "platform_admin" &&
      !confirm(t("confirmPromote", { email: detail.email }))
    )
      return;
    if (
      newRole === "user" &&
      !confirm(t("confirmDemote", { email: detail.email }))
    )
      return;
    try {
      await patch.mutateAsync({ id: detail.id, platform_role: newRole });
      toast.success(t("rolePatched"));
    } catch {
      toast.error(t("patchFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconUsers className="size-4" />
            {detail?.name ?? t("loading")}
          </DialogTitle>
          <DialogDescription>{detail?.email}</DialogDescription>
        </DialogHeader>

        {isLoading || !detail ? (
          <Skeleton className="h-40" />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">
              <Badge
                variant={
                  detail.status === "active"
                    ? "success"
                    : detail.status === "suspended"
                      ? "danger"
                      : "outline"
                }
              >
                {t(`status.${detail.status}`)}
              </Badge>
              <Badge
                variant={
                  detail.platform_role === "platform_admin"
                    ? "warning"
                    : "outline"
                }
              >
                {t(`role.${detail.platform_role}`)}
              </Badge>
              {detail.oauth_provider && (
                <Badge variant="outline">SSO · {detail.oauth_provider}</Badge>
              )}
            </div>

            <div className="grid gap-1.5">
              <Label className="text-[11px] sh-muted">
                {t("workspacesLabel", { n: detail.workspaces.length })}
              </Label>
              {detail.workspaces.length === 0 ? (
                <p className="text-[11px] sh-muted">{t("noWorkspaces")}</p>
              ) : (
                <ul className="flex flex-col gap-1">
                  {detail.workspaces.map((w) => (
                    <li
                      key={w.id}
                      className="flex items-center gap-2 rounded border px-2 py-1 text-xs"
                    >
                      <span className="flex-1 font-medium">{w.name}</span>
                      <span className="font-mono text-[10px] sh-muted">
                        {w.slug}
                      </span>
                      <Badge variant="outline">{w.role}</Badge>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <Link
              href={`/admin/identities/${detail.id}/profile`}
              className="inline-flex items-center gap-1.5 rounded border px-2 py-1 text-xs underline sh-muted hover:bg-black/5 dark:hover:bg-white/10"
              onClick={() => onOpenChange(false)}
            >
              <IconBrain className="size-3.5" />
              {tProfile("viewLink")}
            </Link>
          </div>
        )}

        <DialogFooter>
          {detail && !isSelf && (
            <div className="flex w-full flex-wrap gap-2">
              {detail.status === "active" ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggleStatus("suspended")}
                  disabled={patch.isPending}
                >
                  {patch.isPending ? (
                    <IconLoader2 className="size-4 animate-spin" />
                  ) : (
                    <IconUserPause className="size-4" />
                  )}
                  {t("suspend")}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggleStatus("active")}
                  disabled={patch.isPending}
                >
                  <IconCheck className="size-4" />
                  {t("activate")}
                </Button>
              )}

              {detail.platform_role === "platform_admin" ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggleAdmin("user")}
                  disabled={patch.isPending}
                >
                  <IconShieldOff className="size-4" />
                  {t("demote")}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => toggleAdmin("platform_admin")}
                  disabled={patch.isPending}
                >
                  <IconUserPlus className="size-4" />
                  {t("promote")}
                </Button>
              )}

              <Button
                className="ml-auto"
                onClick={() => onOpenChange(false)}
              >
                {t("close")}
              </Button>
            </div>
          )}
          {(isSelf || !detail) && (
            <Button onClick={() => onOpenChange(false)}>{t("close")}</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
