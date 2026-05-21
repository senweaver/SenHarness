"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { IconCheck, IconLoader2 } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
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
import { Textarea } from "@/components/ui/textarea";
import {
  InlinePicker,
  type InlinePickerOption,
} from "@/components/agents/tabs/pickers/InlinePicker";
import { useCreateAgent } from "@/hooks/use-agent-mutations";
import { useWorkspaceModelOptions } from "@/hooks/use-agent-models";
import { useBackendAdapters } from "@/hooks/use-backend-adapters";
import { useRegisteredRuntimes } from "@/hooks/use-runtimes";
import { cn } from "@/lib/utils";

interface BlankAgentDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

type Visibility = "workspace" | "private";

export function BlankAgentDialog({ open, onOpenChange }: BlankAgentDialogProps) {
  const t = useTranslations("newAgent");
  const tBlank = useTranslations("newAgent.blank");
  const tCommon = useTranslations("common");
  const router = useRouter();
  const create = useCreateAgent();
  const modelsQ = useWorkspaceModelOptions();
  const runtimesQ = useRegisteredRuntimes();
  const adaptersQ = useBackendAdapters();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultModel, setDefaultModel] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<Visibility>("private");
  const [backendKind, setBackendKind] = useState<string>("native");
  const [backendAdapterId, setBackendAdapterId] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setName("");
      setDescription("");
      setDefaultModel(null);
      setVisibility("private");
      setBackendKind("native");
      setBackendAdapterId(null);
    }
  }, [open]);

  const modelOptions = useMemo<InlinePickerOption<string>[]>(() => {
    const rows = modelsQ.data ?? [];
    return rows.map((r) => ({
      value: r.id,
      label: `${r.provider}/${r.name}`,
      description: r.description || undefined,
    }));
  }, [modelsQ.data]);

  const runtimeOptions = useMemo<InlinePickerOption<string>[]>(
    () =>
      (runtimesQ.data ?? []).map((r) => ({
        value: r.kind,
        label: r.display_name || r.kind,
        description: r.description || undefined,
      })),
    [runtimesQ.data],
  );

  const activeRuntime = runtimesQ.data?.find((r) => r.kind === backendKind);
  const requiresAdapter = Boolean(activeRuntime?.requires_adapter);

  const adapterOptions = useMemo<InlinePickerOption<string>[]>(() => {
    const list = adaptersQ.data ?? [];
    return list
      .filter((a) => a.kind === backendKind || a.kind === "openclaw")
      .map((a) => ({
        value: a.id,
        label: a.name,
        description: a.endpoint ?? undefined,
      }));
  }, [adaptersQ.data, backendKind]);

  const adapterMissing = requiresAdapter && adapterOptions.length === 0;
  const disableSubmit =
    create.isPending ||
    !name.trim() ||
    (requiresAdapter && !backendAdapterId);

  const submit = async () => {
    if (!name.trim()) {
      toast.error(t("missingName"));
      return;
    }
    try {
      const created = await create.mutateAsync({
        name: name.trim(),
        description: description.trim() || null,
        default_model: requiresAdapter ? null : defaultModel,
        visibility,
        backend_kind: backendKind as "native" | "openclaw",
        backend_adapter_id: requiresAdapter ? backendAdapterId : null,
      });
      toast.success(t("created"));
      onOpenChange(false);
      router.push(`/agents/${created.id}`);
    } catch {
      toast.error(t("createFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{tBlank("title")}</DialogTitle>
          <DialogDescription>{tBlank("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="blank-agent-name">{tBlank("nameLabel")}</Label>
            <Input
              id="blank-agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={tBlank("namePlaceholder")}
              maxLength={128}
              autoFocus
              data-testid="blank-agent-name"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="blank-agent-description">
              {tBlank("descLabel")}
            </Label>
            <Textarea
              id="blank-agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              placeholder={tBlank("descPlaceholder")}
            />
          </div>

          <div className="space-y-1.5">
            <Label>{tBlank("visibility.label")}</Label>
            <div role="radiogroup" className="grid grid-cols-2 gap-2">
              <VisibilityCard
                selected={visibility === "workspace"}
                title={tBlank("visibility.workspace")}
                hint={tBlank("visibility.workspaceHint")}
                onSelect={() => setVisibility("workspace")}
              />
              <VisibilityCard
                selected={visibility === "private"}
                title={tBlank("visibility.private")}
                hint={tBlank("visibility.privateHint")}
                onSelect={() => setVisibility("private")}
              />
            </div>
          </div>

          <div className="flex items-center justify-between gap-3">
            <Label>{tBlank("runtimeLabel")}</Label>
            <InlinePicker
              label={tBlank("runtimeLabel")}
              value={backendKind}
              options={runtimeOptions}
              onChange={(next) => {
                setBackendKind(next);
                setBackendAdapterId(null);
              }}
            />
          </div>

          {requiresAdapter ? (
            <div className="space-y-1.5">
              <div className="flex items-center justify-between gap-3">
                <Label>{tBlank("adapterLabel")}</Label>
                <InlinePicker
                  label={tBlank("adapterLabel")}
                  value={backendAdapterId}
                  options={adapterOptions}
                  onChange={(next) => setBackendAdapterId(next)}
                  placeholder={tBlank("adapterEmpty")}
                  disabled={adapterOptions.length === 0}
                />
              </div>
              {adapterMissing ? (
                <p className="text-[11px] sh-muted">
                  {tBlank("adapterMissingHint")}
                </p>
              ) : null}
              <p className="text-[11px] sh-muted">{tBlank("remoteModelHint")}</p>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-3">
              <Label>{tBlank("modelLabel")}</Label>
              <InlinePicker
                label={tBlank("modelLabel")}
                value={defaultModel}
                options={modelOptions}
                onChange={(next) => setDefaultModel(next)}
                placeholder={tBlank("modelEmpty")}
              />
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={disableSubmit}
            data-testid="blank-agent-create"
          >
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {tBlank("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function VisibilityCard({
  selected,
  title,
  hint,
  onSelect,
}: {
  selected: boolean;
  title: string;
  hint: string;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "flex w-full items-start gap-2 rounded-md border p-3 text-left transition-colors hover:bg-black/5 dark:hover:bg-white/5",
        selected &&
          "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary)/0.06)]",
      )}
    >
      <span
        className={cn(
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
          selected
            ? "border-[rgb(var(--color-primary))] bg-[rgb(var(--color-primary))] text-white"
            : "border-current sh-muted",
        )}
      >
        {selected ? <IconCheck className="size-3" /> : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-[13px] font-medium">{title}</span>
        <span className="mt-0.5 block text-[11px] sh-muted">{hint}</span>
      </span>
    </button>
  );
}
