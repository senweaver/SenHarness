"use client";

import { useState } from "react";
import { IconLoader2, IconPlus } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Button } from "@/components/ui/button";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { type ChannelKind, useChannels } from "@/hooks/use-channels";
import { ChannelCard } from "@/components/channels/ChannelCard";
import { ChannelCreateForm } from "@/components/channels/ChannelCreateForm";
import { KindPicker } from "@/components/channels/KindPicker";

interface ChannelsTabProps {
  agentId: string;
}

export function ChannelsTab({ agentId }: ChannelsTabProps) {
  const t = useTranslations("agentDetail.channels");
  const { data: channels, isLoading } = useChannels();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [pickedKind, setPickedKind] = useState<ChannelKind | null>(null);

  const bound = (channels ?? []).filter(
    (c) => c.default_agent_id === agentId,
  );

  const openDrawer = (kind: ChannelKind | null) => {
    setPickedKind(kind);
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    setPickedKind(null);
  };

  return (
    <div className="space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <p className="text-[12px] sh-muted">{t("subtitle")}</p>
        </div>
        {bound.length > 0 && (
          <Button size="sm" onClick={() => openDrawer(null)}>
            <IconPlus className="size-4" />
            {t("addCta")}
          </Button>
        )}
      </header>

      {isLoading ? (
        <div className="flex items-center justify-center rounded-md border p-6 text-[13px] sh-muted">
          <IconLoader2 className="size-4 animate-spin" />
        </div>
      ) : bound.length === 0 ? (
        <div className="space-y-3 rounded-md border border-dashed p-6">
          <div className="text-center">
            <p className="text-sm font-medium">{t("boundEmpty")}</p>
            <p className="mt-1 text-[12px] sh-muted">{t("emptyHint")}</p>
          </div>
          <KindPicker onPick={(kind) => openDrawer(kind)} />
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {bound.map((c) => (
            <ChannelCard key={c.id} ch={c} />
          ))}
        </div>
      )}

      <Sheet open={drawerOpen} onOpenChange={(o) => (o ? setDrawerOpen(true) : closeDrawer())}>
        <SheetContent
          side="right"
          className="w-full max-w-2xl overflow-y-auto sm:max-w-3xl"
        >
          <SheetHeader>
            <SheetTitle>{t("drawerTitle")}</SheetTitle>
            <SheetDescription>{t("drawerDescription")}</SheetDescription>
          </SheetHeader>

          <div className="pt-4">
            {pickedKind ? (
              <ChannelCreateForm
                kind={pickedKind}
                lockedAgentId={agentId}
                onBack={() => setPickedKind(null)}
                onDone={closeDrawer}
              />
            ) : (
              <KindPicker onPick={setPickedKind} />
            )}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  );
}
