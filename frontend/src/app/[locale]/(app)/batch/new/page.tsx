"use client";

import { useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { IconLoader2, IconPlus, IconTrash } from "@tabler/icons-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { PageHeader } from "@/components/ui/page-header";
import { useAgents } from "@/hooks/use-agents";
import {
  useCreateBatchRun,
  type BatchCaseInput,
} from "@/hooks/use-batch";

interface CaseRow {
  label: string;
  text: string;
}

export default function NewBatchRunPage() {
  const t = useTranslations("batch.new");
  const router = useRouter();
  const create = useCreateBatchRun();
  const { data: agents } = useAgents();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [agentId, setAgentId] = useState("");
  const [cases, setCases] = useState<CaseRow[]>([
    { label: "case 1", text: "" },
  ]);

  const submit = async () => {
    if (!name.trim() || !agentId) {
      toast.error(t("missingFields"));
      return;
    }
    const payloadCases: BatchCaseInput[] = cases
      .map((c) => ({
        label: c.label.trim() || undefined,
        text: c.text.trim(),
      }))
      .filter((c) => (c.text ?? "").length > 0);
    if (payloadCases.length === 0) {
      toast.error(t("noCases"));
      return;
    }
    try {
      const run = await create.mutateAsync({
        name: name.trim(),
        description: description.trim() || undefined,
        agent_id: agentId,
        cases: payloadCases,
      });
      toast.success(t("created"));
      router.push(`/batch/${run.id}`);
    } catch {
      toast.error(t("createFailed"));
    }
  };

  return (
    <div className="p-6">
      <PageHeader title={t("title")} description={t("description")} />

      <div className="grid gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("runSection")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="grid gap-1.5">
              <Label htmlFor="name">{t("name")}</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("namePlaceholder")}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="description">{t("descriptionField")}</Label>
              <Textarea
                id="description"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t("descriptionPlaceholder")}
              />
            </div>
            <div className="grid gap-1.5">
              <Label>{t("agent")}</Label>
              <Select value={agentId} onValueChange={setAgentId}>
                <SelectTrigger>
                  <SelectValue placeholder={t("agentPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {(agents ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between gap-2 text-base">
              {t("casesSection")}
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  setCases((prev) => [
                    ...prev,
                    { label: `case ${prev.length + 1}`, text: "" },
                  ])
                }
              >
                <IconPlus className="size-4" />
                {t("addCase")}
              </Button>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {cases.map((c, idx) => (
              <div
                key={idx}
                className="space-y-2 rounded-md border p-3"
              >
                <div className="flex items-center gap-2">
                  <Input
                    value={c.label}
                    onChange={(e) =>
                      setCases((prev) =>
                        prev.map((row, i) =>
                          i === idx ? { ...row, label: e.target.value } : row,
                        ),
                      )
                    }
                    placeholder={t("casLabelPlaceholder")}
                    className="max-w-[200px]"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    onClick={() =>
                      setCases((prev) => prev.filter((_, i) => i !== idx))
                    }
                    disabled={cases.length === 1}
                  >
                    <IconTrash className="size-3.5" />
                  </Button>
                </div>
                <Textarea
                  value={c.text}
                  onChange={(e) =>
                    setCases((prev) =>
                      prev.map((row, i) =>
                        i === idx ? { ...row, text: e.target.value } : row,
                      ),
                    )
                  }
                  placeholder={t("caseTextPlaceholder")}
                  className="min-h-[80px] font-mono text-[13px]"
                />
              </div>
            ))}
          </CardContent>
        </Card>

        <div className="flex items-center justify-end gap-2">
          <Button variant="ghost" onClick={() => router.back()}>
            {t("cancel")}
          </Button>
          <Button onClick={submit} disabled={create.isPending}>
            {create.isPending && <IconLoader2 className="size-4 animate-spin" />}
            {t("submit")}
          </Button>
        </div>
      </div>
    </div>
  );
}
