"use client";

import { useState } from "react";
import { IconPlus } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { type ChannelKind, useChannels } from "@/hooks/use-channels";
import { ChannelCard } from "@/components/channels/ChannelCard";
import { ChannelCreateForm } from "@/components/channels/ChannelCreateForm";
import { KindPicker } from "@/components/channels/KindPicker";
import { RateLimitHitsCard } from "@/components/channels/RateLimitHitsCard";

export default function ChannelsPage() {
  const t = useTranslations("settings.channels");
  const tCommon = useTranslations("common");
  const { data, isLoading } = useChannels();
  const [creating, setCreating] = useState(false);
  const [pickedKind, setPickedKind] = useState<ChannelKind | null>(null);

  const cancelCreate = () => {
    setCreating(false);
    setPickedKind(null);
  };

  return (
    <div className="p-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <Button size="sm" onClick={() => (creating ? cancelCreate() : setCreating(true))}>
            <IconPlus className="size-4" />
            {creating ? tCommon("cancel") : t("new")}
          </Button>
        }
      />

      {creating && (
        <Card className="mb-3">
          <CardContent className="py-4">
            {pickedKind ? (
              <ChannelCreateForm
                kind={pickedKind}
                onBack={() => setPickedKind(null)}
                onDone={cancelCreate}
              />
            ) : (
              <KindPicker onPick={setPickedKind} />
            )}
          </CardContent>
        </Card>
      )}

      {isLoading && <Skeleton className="h-40" />}

      {!isLoading && (data ?? []).length === 0 && !creating && (
        <Card>
          <CardContent className="py-10 text-center text-sm sh-muted">
            {t("empty")}
          </CardContent>
        </Card>
      )}

      <div className="flex flex-col gap-2">
        {(data ?? []).map((ch) => (
          <div key={ch.id} className="flex flex-col gap-2">
            <ChannelCard ch={ch} />
            <RateLimitHitsCard channelId={ch.id} />
          </div>
        ))}
      </div>
    </div>
  );
}
