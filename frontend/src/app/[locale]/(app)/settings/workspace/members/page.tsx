"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconCopy,
  IconLoader2,
  IconTrash,
  IconUserPlus,
} from "@tabler/icons-react";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
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
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import {
  useCreateInvitation,
  useInvitations,
  useMembers,
  useRemoveMember,
  useRevokeInvitation,
  useUpdateMember,
} from "@/hooks/use-members";
import { useMe } from "@/hooks/use-me";
import { useDepartments } from "@/hooks/use-departments";

const ROLES = ["owner", "admin", "operator", "member", "auditor", "guest"];
const NO_DEPT = "__none__";

export default function MembersSettingsPage() {
  const t = useTranslations("settings.members");
  const tSettings = useTranslations("settings");
  const { data: members, isLoading: loadingM } = useMembers();
  const { data: invites, isLoading: loadingI } = useInvitations();
  const { data: departments = [] } = useDepartments();
  const { data: me } = useMe();
  const updateMember = useUpdateMember();
  const removeMember = useRemoveMember();
  const revoke = useRevokeInvitation();

  return (
    <div>
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={<InviteDialog />}
      />

      {/* ─── Members ─── */}
      <Card>
        <CardHeader>
          <CardTitle>{t("title")}</CardTitle>
          <CardDescription>{t("description")}</CardDescription>
        </CardHeader>
        <CardContent className="pt-0">
          {loadingM && <Skeleton className="h-20" />}
          {!loadingM && (members ?? []).length <= 1 && (
            <p className="py-2 text-sm sh-muted">{t("noMembers")}</p>
          )}
          <div className="divide-y">
            {(members ?? []).map((m) => {
              const isSelf = m.identity_id === me?.id;
              const displayName = m.identity_name ?? m.identity_id.slice(0, 8);
              const initial = (displayName || "?").slice(0, 1).toUpperCase();
              return (
                <div key={m.id} className="flex items-center gap-3 py-2">
                  <Avatar className="size-8 shrink-0">
                    {m.identity_avatar_url && (
                      <AvatarImage
                        src={m.identity_avatar_url}
                        alt={displayName}
                      />
                    )}
                    <AvatarFallback className="text-xs">{initial}</AvatarFallback>
                  </Avatar>
                  <div className="flex-1 min-w-0 text-sm">
                    <div className="truncate">
                      {displayName}
                      {isSelf && (
                        <span className="ml-1 text-[10px] sh-muted">(you)</span>
                      )}
                    </div>
                    <div className="truncate text-[11px] sh-muted">
                      {m.identity_email ?? "—"} ·{" "}
                      {t("joinedOn", {
                        date: new Date(m.created_at).toLocaleDateString(),
                      })}{" "}
                      · {m.status}
                    </div>
                  </div>
                  <Select
                    value={m.role}
                    disabled={isSelf || updateMember.isPending}
                    onValueChange={async (v) => {
                      try {
                        await updateMember.mutateAsync({
                          identity_id: m.identity_id,
                          role: v,
                        });
                        toast.success(tSettings("saved"));
                      } catch {
                        toast.error(tSettings("saveFailed"));
                      }
                    }}
                  >
                    <SelectTrigger className="h-8 w-28 text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {ROLES.map((r) => (
                        <SelectItem key={r} value={r}>
                          {t(("role" + r.charAt(0).toUpperCase() + r.slice(1)) as "roleOwner")}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={m.department_id ?? NO_DEPT}
                    disabled={updateMember.isPending}
                    onValueChange={async (v) => {
                      try {
                        await updateMember.mutateAsync({
                          identity_id: m.identity_id,
                          department_id: v === NO_DEPT ? null : v,
                        });
                        toast.success(tSettings("saved"));
                      } catch {
                        toast.error(tSettings("saveFailed"));
                      }
                    }}
                  >
                    <SelectTrigger className="h-8 w-32 text-xs">
                      <SelectValue placeholder="—" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={NO_DEPT}>{t("noDepartment")}</SelectItem>
                      {departments.map((d) => (
                        <SelectItem key={d.id} value={d.id}>
                          {d.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {!isSelf && (
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7"
                      aria-label={t("removeMember")}
                      onClick={async () => {
                        if (!confirm(t("confirmRemove"))) return;
                        try {
                          await removeMember.mutateAsync(m.identity_id);
                          toast.success(tSettings("deleted"));
                        } catch {
                          toast.error(tSettings("deleteFailed"));
                        }
                      }}
                    >
                      <IconTrash className="size-3.5" />
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* ─── Pending invites ─── */}
      <Card className="mt-4">
        <CardHeader>
          <CardTitle>{t("invitePending")}</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {loadingI && <Skeleton className="h-16" />}
          {!loadingI && (invites ?? []).filter((i) => !i.used_at).length === 0 && (
            <p className="py-2 text-sm sh-muted">{t("noPending")}</p>
          )}
          <div className="divide-y">
            {(invites ?? [])
              .filter((i) => !i.used_at)
              .map((inv) => {
                const link =
                  typeof window !== "undefined"
                    ? `${window.location.origin}/invite/${inv.code}`
                    : `/invite/${inv.code}`;
                return (
                  <div key={inv.id} className="flex items-center gap-3 py-2">
                    <div className="flex-1 min-w-0 text-sm">
                      <div className="flex items-center gap-2">
                        <span className="truncate">{inv.email ?? t("anyEmail")}</span>
                        <Badge variant="outline">{inv.role}</Badge>
                      </div>
                      <div className="text-[11px] sh-muted">
                        {t("codeLabel")} {inv.code.slice(0, 8)}… · {t("expiresLabel")}{" "}
                        {inv.expires_at
                          ? new Date(inv.expires_at).toLocaleString()
                          : t("expiresNever")}
                      </div>
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        void navigator.clipboard.writeText(link);
                        toast.success(t("copied"));
                      }}
                    >
                      <IconCopy className="size-3.5" /> {t("copy")}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-7"
                      onClick={async () => {
                        if (!confirm(tSettings("confirmDelete"))) return;
                        try {
                          await revoke.mutateAsync(inv.id);
                          toast.success(tSettings("deleted"));
                        } catch {
                          toast.error(tSettings("deleteFailed"));
                        }
                      }}
                    >
                      <IconTrash className="size-3.5" />
                    </Button>
                  </div>
                );
              })}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function InviteDialog() {
  const t = useTranslations("settings.members");
  const tSettings = useTranslations("settings");
  const create = useCreateInvitation();
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [hours, setHours] = useState(72);
  const [link, setLink] = useState<string | null>(null);

  const submit = async () => {
    try {
      const inv = await create.mutateAsync({
        email: email || null,
        role,
        expires_in_hours: hours,
      });
      toast.success(tSettings("created"));
      const url = `${window.location.origin}/invite/${inv.code}`;
      setLink(url);
    } catch {
      toast.error(tSettings("createFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { setOpen(v); if (!v) setLink(null); }}>
      <DialogTrigger asChild>
        <Button size="sm">
          <IconUserPlus className="size-4" />
          {t("invite")}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("invite")}</DialogTitle>
          <DialogDescription>{t("createInvite")}</DialogDescription>
        </DialogHeader>

        {link ? (
          <div className="space-y-2">
            <Label>{t("inviteLink")}</Label>
            <div className="flex gap-2">
              <Input readOnly value={link} className="font-mono text-xs" />
              <Button
                variant="outline"
                size="icon"
                aria-label={t("copy")}
                onClick={() => {
                  void navigator.clipboard.writeText(link);
                  toast.success(t("copied"));
                }}
              >
                <IconCopy className="size-4" />
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid gap-1.5">
              <Label htmlFor="email">{t("email")}</Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="jane@corp.com"
              />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="grid gap-1.5">
                <Label htmlFor="role">{t("role")}</Label>
                <Select value={role} onValueChange={setRole}>
                  <SelectTrigger id="role">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r}>
                        {t(("role" + r.charAt(0).toUpperCase() + r.slice(1)) as "roleMember")}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="hours">{t("expiresHours")}</Label>
                <Input
                  id="hours"
                  type="number"
                  min={1}
                  max={720}
                  value={hours}
                  onChange={(e) => setHours(Number(e.target.value) || 72)}
                />
              </div>
            </div>
          </div>
        )}

        <DialogFooter>
          {link ? (
            <Button onClick={() => setOpen(false)}>OK</Button>
          ) : (
            <>
              {/* Real bug found in V1 review: this button used to read
                  ``settings.confirmDelete`` ("Confirm delete") which is
                  semantically the OPPOSITE of what it does — it just closes
                  the create-invite dialog. Now uses members.cancel. */}
              <Button variant="ghost" onClick={() => setOpen(false)}>
                {t("cancel")}
              </Button>
              <Button onClick={submit} disabled={create.isPending}>
                {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
                {t("createInvite")}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
