"use client";

import { useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconEye, IconEyeOff, IconLoader2 } from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCreateProvider, type ProviderRead } from "@/hooks/use-providers";

type Protocol = "openai" | "openai_responses" | "anthropic" | "google";

const PROTOCOL_DEFAULT_BASE_URL: Record<Protocol, string> = {
  openai: "https://api.openai.com/v1",
  openai_responses: "https://api.openai.com/v1",
  anthropic: "https://api.anthropic.com",
  google: "https://generativelanguage.googleapis.com/v1beta",
};

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated?: (created: ProviderRead) => void;
}

export function AddCustomProviderDialog({ open, onOpenChange, onCreated }: Props) {
  const t = useTranslations("settings.providers.addCustom");
  const tSettings = useTranslations("settings");
  const tCommon = useTranslations("common");
  const create = useCreateProvider();

  const [name, setName] = useState("");
  const [protocol, setProtocol] = useState<Protocol>("openai");
  const [baseUrl, setBaseUrl] = useState(PROTOCOL_DEFAULT_BASE_URL.openai);
  const [apiKey, setApiKey] = useState("");
  const [defaultModel, setDefaultModel] = useState("");
  const [showKey, setShowKey] = useState(false);

  function handleProtocolChange(p: Protocol) {
    setProtocol(p);
    if (!baseUrl || Object.values(PROTOCOL_DEFAULT_BASE_URL).includes(baseUrl)) {
      setBaseUrl(PROTOCOL_DEFAULT_BASE_URL[p]);
    }
  }

  function reset() {
    setName("");
    setProtocol("openai");
    setBaseUrl(PROTOCOL_DEFAULT_BASE_URL.openai);
    setApiKey("");
    setDefaultModel("");
    setShowKey(false);
  }

  async function submit() {
    if (!name.trim()) {
      toast.error(t("errors.nameRequired"));
      return;
    }
    if (!baseUrl.trim()) {
      toast.error(t("errors.baseUrlRequired"));
      return;
    }
    if (!apiKey.trim()) {
      toast.error(t("errors.apiKeyRequired"));
      return;
    }
    try {
      const created = await create.mutateAsync({
        kind: "custom",
        name: name.trim(),
        base_url: baseUrl.trim(),
        api_key: apiKey.trim(),
        default_model: defaultModel.trim() || null,
        enabled: true,
        metadata_json: {
          source: "custom",
          protocol,
        },
      });
      toast.success(tSettings("created"));
      reset();
      onOpenChange(false);
      onCreated?.(created);
    } catch {
      toast.error(tSettings("createFailed"));
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) reset();
        onOpenChange(o);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-sm">{t("name")}</Label>
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("namePlaceholder")}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">{t("protocol")}</Label>
            <Select
              value={protocol}
              onValueChange={(v) => handleProtocolChange(v as Protocol)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="openai">{t("protocols.openai")}</SelectItem>
                <SelectItem value="openai_responses">
                  {t("protocols.openaiResponses")}
                </SelectItem>
                <SelectItem value="anthropic">
                  {t("protocols.anthropic")}
                </SelectItem>
                <SelectItem value="google">{t("protocols.google")}</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              {t("protocolHint")}
            </p>
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">{t("baseUrl")}</Label>
            <Input
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={PROTOCOL_DEFAULT_BASE_URL[protocol]}
              spellCheck={false}
              autoComplete="off"
            />
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">{t("apiKey")}</Label>
            <div className="relative">
              <Input
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={t("apiKeyPlaceholder")}
                autoComplete="off"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-1 top-1 size-7"
                onClick={() => setShowKey((s) => !s)}
              >
                {showKey ? (
                  <IconEyeOff className="size-3.5" />
                ) : (
                  <IconEye className="size-3.5" />
                )}
              </Button>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label className="text-sm">{t("defaultModel")}</Label>
            <Input
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              placeholder={t("defaultModelPlaceholder")}
              spellCheck={false}
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              {t("defaultModelHint")}
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={submit} disabled={create.isPending}>
            {create.isPending ? (
              <IconLoader2 className="size-4 animate-spin" />
            ) : null}
            {t("submit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
