"use client";

/**
 * Self "USER.md + SOUL.md" page — member-accessible.
 *
 * USER.md is the identity's preferences (self-edit). SOUL.md is passive
 * modelling — every write goes through the per-identity approval queue,
 * which the identity owns.
 */

import { useState } from "react";
import { IconLoader2, IconSparkles } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { PageHeader, SectionHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownProfileEditor } from "@/components/memory-profiles/MarkdownProfileEditor";
import { SoulDimsCard } from "@/components/memory-profiles/SoulDimsCard";
import { SoulPendingQueue } from "@/components/memory-profiles/SoulPendingQueue";
import {
  MEMORY_CAPS,
  useMyMemoryProfiles,
  useProposeSoul,
  usePutMyProfile,
} from "@/hooks/use-memory-profiles";

export default function MySoulPage() {
  const t = useTranslations("settings.soul");
  const { data, isLoading } = useMyMemoryProfiles();
  const putProfile = usePutMyProfile();
  const propose = useProposeSoul();

  const [proposeContent, setProposeContent] = useState("");
  const [proposeRationale, setProposeRationale] = useState("");

  if (isLoading) return <Skeleton className="h-40" />;

  const profile = data?.profile ?? null;
  const soul = data?.soul ?? null;
  const userCap = MEMORY_CAPS.user_profile;
  const soulCap = MEMORY_CAPS.user_soul;

  const saveProfile = async (next: string) => {
    try {
      await putProfile.mutateAsync({ content_md: next });
      toast.success(t("saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    }
  };

  const submitProposal = async () => {
    if (!proposeContent.trim()) return;
    try {
      await propose.mutateAsync({
        proposed_content: proposeContent.trim(),
        rationale: proposeRationale.trim().slice(0, 512),
      });
      toast.success(t("saved"));
      setProposeContent("");
      setProposeRationale("");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("saveFailed"));
    }
  };

  return (
    <div data-testid="soul-page">
      <PageHeader title={t("title")} description={t("description")} />

      <Card className="mb-4">
        <CardHeader>
          <CardTitle className="text-base">{t("userHeading")}</CardTitle>
          <CardDescription>{t("userDescription", { cap: userCap })}</CardDescription>
        </CardHeader>
        <CardContent>
          <MarkdownProfileEditor
            ns="settings.soul"
            initialContent={profile?.content_md ?? ""}
            maxChars={userCap}
            onSave={saveProfile}
            saving={putProfile.isPending}
            submitLabel={t("userSave")}
            placeholder={t("userPlaceholder")}
            testIdPrefix="user-profile"
          />
        </CardContent>
      </Card>

      <SectionHeader title={t("soulHeading")} description={t("soulDescription")} />

      <Card className="mb-4">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">{t("soulCurrent")}</CardTitle>
        </CardHeader>
        <CardContent>
          {soul?.content_md ? (
            <pre className="max-h-[320px] overflow-auto whitespace-pre-wrap rounded border bg-black/2 p-3 font-mono text-[12px] dark:bg-white/5">
              {soul.content_md}
            </pre>
          ) : (
            <p className="py-6 text-center text-sm sh-muted">{t("soulEmpty")}</p>
          )}
        </CardContent>
      </Card>

      {soul && <SoulDimsCard dims={soul.soul_dims_json ?? {}} />}

      <SectionHeader
        className="mt-4"
        title={t("proposeHeading")}
        description={t("proposeDescription")}
      />

      <Card className="mb-4">
        <CardContent className="space-y-3 py-3">
          <div className="grid gap-1.5">
            <Label>{t("proposeContent")}</Label>
            <Textarea
              value={proposeContent}
              onChange={(e) => setProposeContent(e.target.value)}
              placeholder={t("proposePlaceholder")}
              className="min-h-[140px] font-mono text-[13px]"
              data-testid="soul-propose-content"
            />
          </div>
          <div className="grid gap-1.5">
            <Label>{t("proposeRationale")}</Label>
            <Textarea
              value={proposeRationale}
              onChange={(e) => setProposeRationale(e.target.value)}
              className="min-h-[60px]"
              maxLength={512}
              data-testid="soul-propose-rationale"
            />
          </div>
          <div className="flex justify-end">
            <Button
              onClick={submitProposal}
              disabled={propose.isPending || !proposeContent.trim()}
              data-testid="soul-propose-submit"
            >
              {propose.isPending ? (
                <IconLoader2 className="size-4 animate-spin" />
              ) : (
                <IconSparkles className="size-4" />
              )}
              {t("proposeSubmit")}
            </Button>
          </div>
        </CardContent>
      </Card>

      <SectionHeader className="mt-4" title={t("pendingHeading")} />
      <SoulPendingQueue />
    </div>
  );
}
