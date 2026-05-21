"use client";

import { useEffect, useState } from "react";
import { IconLoader2, IconPlus } from "@tabler/icons-react";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  useCreateMemory,
  type MemoryKind,
  type MemoryScope,
} from "@/hooks/use-memories";

interface AddMemoryDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentId: string;
}

/**
 * AddMemoryDialog — drops a memory into the assistant scope keyed
 * to the current agent. The runtime then surfaces it via `recall` /
 * `memorize` tool paths during inference.
 */
export function AddMemoryDialog({
  open,
  onOpenChange,
  agentId,
}: AddMemoryDialogProps) {
  const t = useTranslations("settings.memory");
  const tForm = useTranslations("settings.memory.form");
  const tCommon = useTranslations("common");
  const create = useCreateMemory();

  const [scope, setScope] = useState<MemoryScope>("assistant");
  const [kind, setKind] = useState<MemoryKind>("kv");
  const [key, setKey] = useState("");
  const [content, setContent] = useState("");

  useEffect(() => {
    if (!open) {
      setScope("assistant");
      setKind("kv");
      setKey("");
      setContent("");
    }
  }, [open]);

  const submit = async () => {
    if (!content.trim()) {
      toast.error(t("saveFailed"));
      return;
    }
    try {
      await create.mutateAsync({
        scope,
        scope_id: scope === "assistant" ? agentId : null,
        kind,
        key: kind === "kv" ? key.trim() || null : null,
        content: content.trim(),
        confidence: 0.9,
      });
      toast.success(t("saved"));
      onOpenChange(false);
    } catch {
      toast.error(t("saveFailed"));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconPlus className="size-4" />
            {t("newTitle")}
          </DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>{tForm("scope")}</Label>
              <Select value={scope} onValueChange={(v) => setScope(v as MemoryScope)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="assistant">
                    {t("scope.assistant")}
                  </SelectItem>
                  <SelectItem value="user">{t("scope.user")}</SelectItem>
                  <SelectItem value="workspace">
                    {t("scope.workspace")}
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>{tForm("kind")}</Label>
              <Select value={kind} onValueChange={(v) => setKind(v as MemoryKind)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="kv">{t("kind.kv")}</SelectItem>
                  <SelectItem value="episodic">{t("kind.episodic")}</SelectItem>
                  <SelectItem value="semantic">{t("kind.semantic")}</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {kind === "kv" && (
            <div className="space-y-1.5">
              <Label>{tForm("key")}</Label>
              <Input
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="user.preferred_name"
                className="font-mono text-[12px]"
              />
            </div>
          )}

          <div className="space-y-1.5">
            <Label>{tForm("content")}</Label>
            <Textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={4}
              placeholder={tForm("contentPlaceholder")}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {tCommon("cancel")}
          </Button>
          <Button onClick={() => void submit()} disabled={create.isPending}>
            {create.isPending && (
              <IconLoader2 className="size-4 animate-spin" />
            )}
            {tCommon("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
