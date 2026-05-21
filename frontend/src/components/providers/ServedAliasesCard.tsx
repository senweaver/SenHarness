"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconPlus, IconTrash } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useDeleteServedAlias,
  useServedAliases,
  useUpsertServedAlias,
} from "@/hooks/use-served-aliases";

const SERVED_NAME_PATTERN = /^[A-Za-z0-9._:/-]{1,120}$/;
const UPSTREAM_PATTERN = /^[A-Za-z0-9._:/-]{1,200}$/;

export function ServedAliasesCard() {
  const t = useTranslations("servedModel");
  const tCommon = useTranslations("common");
  const { data, isLoading } = useServedAliases();
  const upsert = useUpsertServedAlias();
  const remove = useDeleteServedAlias();

  const [addOpen, setAddOpen] = useState(false);
  const [servedName, setServedName] = useState("");
  const [upstream, setUpstream] = useState("");
  const [pendingDeleteName, setPendingDeleteName] = useState<string | null>(
    null,
  );

  const aliases = data?.aliases ?? [];
  const submitDisabled =
    !SERVED_NAME_PATTERN.test(servedName.trim()) ||
    !UPSTREAM_PATTERN.test(upstream.trim()) ||
    upsert.isPending;

  async function handleSubmit() {
    try {
      await upsert.mutateAsync({
        served_name: servedName.trim(),
        upstream: upstream.trim(),
      });
      toast.success(t("upsertSuccessToast"));
      setAddOpen(false);
      setServedName("");
      setUpstream("");
    } catch {
      toast.error(t("upsertFailedToast"));
    }
  }

  async function handleDelete(name: string) {
    try {
      await remove.mutateAsync({ served_name: name });
      toast.success(t("deleteSuccessToast"));
    } catch {
      toast.error(t("deleteFailedToast"));
    } finally {
      setPendingDeleteName(null);
    }
  }

  return (
    <section className="border-t bg-card/30 px-6 py-4 text-sm">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="font-medium">{t("aliasesTitle")}</h3>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {t("aliasesDescription")}
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setAddOpen(true)}
          aria-label={t("addAliasButton")}
        >
          <IconPlus className="size-3.5" />
          {t("addAliasButton")}
        </Button>
      </header>

      <div className="mt-3">
        {isLoading ? (
          <p className="text-xs text-muted-foreground">{tCommon("loading")}</p>
        ) : aliases.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t("emptyState")}</p>
        ) : (
          <ul className="divide-y rounded-md border bg-background">
            {aliases.map((alias) => (
              <li
                key={alias.served_name}
                className="flex items-center justify-between gap-3 px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate font-mono text-xs font-medium">
                    {alias.served_name}
                  </div>
                  <div className="truncate font-mono text-[11px] text-muted-foreground">
                    → {alias.upstream}
                  </div>
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  aria-label={t("deleteAliasButton")}
                  onClick={() => setPendingDeleteName(alias.served_name)}
                >
                  <IconTrash className="size-3.5" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <Dialog
        open={addOpen}
        onOpenChange={(open) => {
          if (!open) {
            setAddOpen(false);
            setServedName("");
            setUpstream("");
          }
        }}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("addDialogTitle")}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="served-name">{t("servedNameLabel")}</Label>
              <Input
                id="served-name"
                placeholder={t("servedNamePlaceholder")}
                value={servedName}
                onChange={(e) => setServedName(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground">
                {t("servedNameHelp")}
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="upstream">{t("upstreamLabel")}</Label>
              <Input
                id="upstream"
                placeholder={t("upstreamPlaceholder")}
                value={upstream}
                onChange={(e) => setUpstream(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground">
                {t("upstreamHelp")}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              {tCommon("cancel")}
            </Button>
            <Button disabled={submitDisabled} onClick={handleSubmit}>
              {tCommon("save")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={pendingDeleteName !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDeleteName(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("deleteDialogTitle")}</DialogTitle>
          </DialogHeader>
          <p className="text-sm">
            {t("deleteAliasConfirm", {
              served_name: pendingDeleteName ?? "",
            })}
          </p>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setPendingDeleteName(null)}
            >
              {tCommon("cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending || pendingDeleteName === null}
              onClick={() => {
                if (pendingDeleteName) handleDelete(pendingDeleteName);
              }}
            >
              {tCommon("delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}
