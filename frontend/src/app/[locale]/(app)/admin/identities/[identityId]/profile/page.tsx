"use client";

/**
 * Admin view of another identity's USER.md + SOUL.md.
 *
 * Read-only — admins can see what's been accumulated about a team
 * member but can't edit it (edits require the identity's own decision,
 * per the approval queue on `/settings/profile/soul`).
 */

import { Link } from "@/lib/navigation";
import { use } from "react";
import { IconArrowLeft } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { SoulDimsCard } from "@/components/memory-profiles/SoulDimsCard";
import { useIdentityProfiles } from "@/hooks/use-memory-profiles";

export default function AdminIdentityProfilePage({
  params,
}: {
  params: Promise<{ identityId: string }>;
}) {
  const { identityId } = use(params);
  const t = useTranslations("admin.identityProfile");
  const { data, isLoading, error } = useIdentityProfiles(identityId);

  if (isLoading) {
    return (
      <div>
        <Skeleton className="mb-4 h-8 w-40" />
        <Skeleton className="h-48" />
      </div>
    );
  }

  if (error) {
    return (
      <div data-testid="admin-identity-profile-page">
        <PageHeader title={t("title")} description={t("loadFailed")} />
        <Link
          href="/admin/users"
          className="inline-flex items-center gap-1 text-sm underline"
        >
          <IconArrowLeft className="size-4" /> {t("backToUsers")}
        </Link>
      </div>
    );
  }

  const profile = data?.profile ?? null;
  const soul = data?.soul ?? null;

  return (
    <div data-testid="admin-identity-profile-page">
      <Link
        href="/admin/users"
        className="mb-3 inline-flex items-center gap-1 text-xs underline sh-muted"
      >
        <IconArrowLeft className="size-3" /> {t("backToUsers")}
      </Link>

      <PageHeader title={t("title")} description={t("description")} />

      <Card className="mb-4">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t("userHeading")}</CardTitle>
        </CardHeader>
        <CardContent>
          {profile?.content_md ? (
            <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap rounded border bg-black/2 p-3 font-mono text-[12px] dark:bg-white/5">
              {profile.content_md}
            </pre>
          ) : (
            <p className="py-6 text-center text-sm sh-muted">{t("empty")}</p>
          )}
        </CardContent>
      </Card>

      <Card className="mb-4">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t("soulHeading")}</CardTitle>
        </CardHeader>
        <CardContent>
          {soul?.content_md ? (
            <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap rounded border bg-black/2 p-3 font-mono text-[12px] dark:bg-white/5">
              {soul.content_md}
            </pre>
          ) : (
            <p className="py-6 text-center text-sm sh-muted">{t("empty")}</p>
          )}
        </CardContent>
      </Card>

      {soul && <SoulDimsCard dims={soul.soul_dims_json ?? {}} />}
    </div>
  );
}
