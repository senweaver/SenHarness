"use client";

import { useEffect, useState } from "react";
import {
  IconKey,
  IconLanguage,
  IconLoader2,
  IconRefresh,
  IconUser,
} from "@tabler/icons-react";
import { useRouter } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import { MfaCard } from "@/components/auth/MfaCard";
import { ProfileTabs, type ProfileTabKey } from "@/components/settings/ProfileTabs";
import { SoulProfileBody } from "@/components/settings/SoulProfileBody";
import { useOnboardingStore } from "@/stores/onboarding-store";
import { useMe } from "@/hooks/use-me";
import { useChangePassword, useUpdateMe } from "@/hooks/use-me-mutations";

export default function ProfileSettingsPage() {
  const t = useTranslations("settings.profile");
  const { data: me } = useMe();
  const searchParams = useSearchParams();
  const tab: ProfileTabKey = searchParams.get("tab") === "soul" ? "soul" : "profile";

  return (
    <div>
      <ProfileTabs active={tab} />
      {tab === "soul" ? (
        <SoulProfileBody />
      ) : (
        <>
          <PageHeader title={t("title")} description={t("description")} />
          <div className="grid gap-4 lg:grid-cols-2">
            <ProfileCard />
            <PasswordCard canChange={!me?.oauth_provider} />
            <div className="lg:col-span-2">
              <LanguageCard />
            </div>
            <div className="lg:col-span-2">
              <MfaCard isSso={Boolean(me?.oauth_provider)} />
            </div>
            <div className="lg:col-span-2">
              <OnboardingResetCard />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function LanguageCard() {
  const t = useTranslations("settings.account.language");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const { data: me } = useMe();
  const update = useUpdateMe();
  const [value, setValue] = useState<string>("");

  useEffect(() => {
    setValue(me?.preferred_locale ?? "");
  }, [me?.preferred_locale]);

  const onSave = async () => {
    try {
      await update.mutateAsync({ preferred_locale: value });
      if (typeof document !== "undefined") {
        const cleaned = value.trim();
        const ttl = 60 * 60 * 24 * 365;
        if (cleaned) {
          document.cookie = `NEXT_LOCALE=${cleaned}; Path=/; Max-Age=${ttl}; SameSite=Lax`;
        } else {
          document.cookie = "NEXT_LOCALE=; Path=/; Max-Age=0; SameSite=Lax";
        }
      }
      toast.success(t("saved"));
      router.refresh();
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  const dirty = (me?.preferred_locale ?? "") !== value;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <IconLanguage className="size-4" />
          {t("title")}
        </CardTitle>
        <CardDescription>{t("description")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid gap-1.5">
          <Label htmlFor="profile-language">{t("label")}</Label>
          <Select value={value || "__platform__"} onValueChange={(v) => setValue(v === "__platform__" ? "" : v)}>
            <SelectTrigger id="profile-language">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__platform__">{t("usePlatformDefault")}</SelectItem>
              <SelectItem value="en-US">English (en-US)</SelectItem>
              <SelectItem value="zh-CN">简体中文 (zh-CN)</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-[11px] sh-muted">{t("hint")}</p>
        </div>
        <div className="flex justify-end">
          <Button onClick={onSave} disabled={!dirty || update.isPending}>
            {update.isPending && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function OnboardingResetCard() {
  const t = useTranslations("settings.profile");
  const router = useRouter();
  const restart = useOnboardingStore((s) => s.restart);
  const onReplay = () => {
    restart();
    toast.success(t("onboardingReplayed"));
    router.push("/");
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <IconRefresh className="size-4" />
          {t("onboardingTitle")}
        </CardTitle>
        <CardDescription>{t("onboardingDesc")}</CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" onClick={onReplay}>
          {t("onboardingReplay")}
        </Button>
      </CardContent>
    </Card>
  );
}

function ProfileCard() {
  const t = useTranslations("settings.profile");
  const tSettings = useTranslations("settings");
  const { data: me } = useMe();
  const update = useUpdateMe();

  const [name, setName] = useState(me?.name ?? "");
  const [avatarUrl, setAvatarUrl] = useState(me?.avatar_url ?? "");

  useEffect(() => {
    if (!me) return;
    setName(me.name);
    setAvatarUrl(me.avatar_url ?? "");
  }, [me?.id]);

  const dirty = name !== (me?.name ?? "") || avatarUrl !== (me?.avatar_url ?? "");

  const onSave = async () => {
    try {
      await update.mutateAsync({
        name: name.trim() || undefined,
        avatar_url: avatarUrl.trim() || null,
      });
      toast.success(tSettings("saved"));
    } catch {
      toast.error(tSettings("saveFailed"));
    }
  };

  const initial = (name || "U").slice(0, 1).toUpperCase();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <IconUser className="size-4" />
          {t("identity")}
        </CardTitle>
        <CardDescription>{t("identityDesc")}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-4">
          <Avatar className="size-16 text-xl">
            {avatarUrl && <AvatarImage src={avatarUrl} alt={name} />}
            <AvatarFallback>{initial}</AvatarFallback>
          </Avatar>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">
              {me?.name ?? "—"}
            </div>
            <div className="truncate text-[12px] sh-muted">{me?.email}</div>
            <div className="mt-1 flex flex-wrap gap-1">
              {me?.platform_role === "platform_admin" && (
                <Badge variant="warning">platform admin</Badge>
              )}
              {me?.oauth_provider && (
                <Badge variant="outline">SSO · {me.oauth_provider}</Badge>
              )}
              <Badge variant="outline">{me?.status}</Badge>
            </div>
          </div>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1.5">
            <Label htmlFor="profile-name">{t("name")}</Label>
            <Input
              id="profile-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="profile-email">{t("email")}</Label>
            <Input
              id="profile-email"
              value={me?.email ?? ""}
              disabled
              className="cursor-not-allowed"
            />
            <p className="text-[11px] sh-muted">{t("emailHint")}</p>
          </div>
        </div>

        <div className="grid gap-1.5">
          <Label htmlFor="profile-avatar">{t("avatarUrl")}</Label>
          <Input
            id="profile-avatar"
            value={avatarUrl}
            onChange={(e) => setAvatarUrl(e.target.value)}
            placeholder="https://…"
          />
          <p className="text-[11px] sh-muted">{t("avatarHint")}</p>
        </div>

        <div className="flex justify-end">
          <Button onClick={onSave} disabled={!dirty || update.isPending}>
            {update.isPending && <IconLoader2 className="size-4 animate-spin" />}
            {t("save")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function PasswordCard({ canChange }: { canChange: boolean }) {
  const t = useTranslations("settings.profile");
  const change = useChangePassword();

  const [oldPw, setOldPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");

  const onSubmit = async () => {
    if (newPw.length < 8) {
      toast.error(t("pwTooShort"));
      return;
    }
    if (newPw !== confirmPw) {
      toast.error(t("pwMismatch"));
      return;
    }
    try {
      await change.mutateAsync({ old_password: oldPw, new_password: newPw });
      toast.success(t("pwChanged"));
      setOldPw("");
      setNewPw("");
      setConfirmPw("");
    } catch {
      toast.error(t("pwFailed"));
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <IconKey className="size-4" />
          {t("password")}
        </CardTitle>
        <CardDescription>
          {canChange ? t("passwordDesc") : t("ssoAccountHint")}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <fieldset disabled={!canChange} className="space-y-3 disabled:opacity-50">
          <div className="grid gap-1.5">
            <Label htmlFor="old-pw">{t("oldPassword")}</Label>
            <Input
              id="old-pw"
              type="password"
              value={oldPw}
              onChange={(e) => setOldPw(e.target.value)}
              autoComplete="current-password"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-pw">{t("newPassword")}</Label>
            <Input
              id="new-pw"
              type="password"
              value={newPw}
              onChange={(e) => setNewPw(e.target.value)}
              autoComplete="new-password"
            />
            <p className="text-[11px] sh-muted">{t("pwRule")}</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="confirm-pw">{t("confirmPassword")}</Label>
            <Input
              id="confirm-pw"
              type="password"
              value={confirmPw}
              onChange={(e) => setConfirmPw(e.target.value)}
              autoComplete="new-password"
            />
          </div>

          <div className="flex justify-end">
            <Button
              onClick={onSubmit}
              disabled={
                change.isPending || !oldPw || !newPw || !confirmPw
              }
            >
              {change.isPending && (
                <IconLoader2 className="size-4 animate-spin" />
              )}
              {t("changePassword")}
            </Button>
          </div>
        </fieldset>
      </CardContent>
    </Card>
  );
}
