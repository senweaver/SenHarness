"use client";

import { forwardRef, useEffect, useRef, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useSearchParams } from "next/navigation";
import { IconCheck, IconLoader2, IconRobot } from "@tabler/icons-react";
import { useLocale, useTranslations } from "next-intl";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { AutonomyPicker } from "@/components/agents/tabs/pickers/AutonomyPicker";
import { DefaultModelPicker } from "@/components/agents/tabs/pickers/DefaultModelPicker";
import { RuntimePicker } from "@/components/agents/tabs/pickers/RuntimePicker";
import { SandboxPicker } from "@/components/agents/tabs/pickers/SandboxPicker";
import { VisibilityPicker } from "@/components/agents/tabs/pickers/VisibilityPicker";
import { useAgentModels } from "@/hooks/use-agent-models";
import { useUpdateAgent } from "@/hooks/use-agent-mutations";
import { relativeTime } from "@/lib/utils";
import type { AgentRead } from "@/types/api";

const MAX_TOOL_ROUNDS_DEFAULT = 50;
const MAX_TOOL_ROUNDS_MIN = 1;
const MAX_TOOL_ROUNDS_MAX = 500;
const MAX_CONCURRENT_TASKS_DEFAULT = 5;
const MAX_CONCURRENT_TASKS_MIN = 1;
const MAX_CONCURRENT_TASKS_MAX = 50;

function readMetadataNumber(
  meta: Record<string, unknown>,
  key: string,
  fallback: number,
  min: number,
  max: number,
): number {
  const raw = meta[key];
  const n = typeof raw === "number" ? raw : Number(raw ?? fallback);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

function readFallbackModel(meta: Record<string, unknown>): string {
  return typeof meta.fallback_model === "string" ? meta.fallback_model : "";
}

interface OverviewTabProps {
  agent: AgentRead;
}

/**
 * OverviewTab — readonly identity card + inline-editable persona.
 *
 * Plan §3: persona / description edits commit on blur (no explicit
 * Save button). The legacy `?edit=1` query is honoured by jumping
 * the cursor straight into the persona textarea so deep links from
 * the old `/agents/[id]/edit` route still feel like an "edit" entry
 * point.
 */
export function OverviewTab({ agent }: OverviewTabProps) {
  const t = useTranslations("settings.agents.detail");
  const tCommon = useTranslations("common");
  const locale = useLocale();
  const router = useRouter();
  const searchParams = useSearchParams();
  const wantsEdit = searchParams.get("edit") === "1";

  const update = useUpdateAgent(agent.id);
  const personaRef = useRef<HTMLTextAreaElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  const [persona, setPersona] = useState(agent.persona_md ?? "");
  const [description, setDescription] = useState(agent.description ?? "");
  const [savingField, setSavingField] = useState<string | null>(null);
  const [savedField, setSavedField] = useState<string | null>(null);

  const initialMeta = (agent.metadata_json ?? {}) as Record<string, unknown>;
  const [maxToolRounds, setMaxToolRounds] = useState<string>(
    String(
      readMetadataNumber(
        initialMeta,
        "max_tool_rounds",
        MAX_TOOL_ROUNDS_DEFAULT,
        MAX_TOOL_ROUNDS_MIN,
        MAX_TOOL_ROUNDS_MAX,
      ),
    ),
  );
  const [maxConcurrentTasks, setMaxConcurrentTasks] = useState<string>(
    String(
      readMetadataNumber(
        initialMeta,
        "max_concurrent_tasks",
        MAX_CONCURRENT_TASKS_DEFAULT,
        MAX_CONCURRENT_TASKS_MIN,
        MAX_CONCURRENT_TASKS_MAX,
      ),
    ),
  );
  const [fallbackModel, setFallbackModel] = useState<string>(
    readFallbackModel(initialMeta),
  );

  const modelsQ = useAgentModels(agent.id);

  useEffect(() => {
    setPersona(agent.persona_md ?? "");
    setDescription(agent.description ?? "");
  }, [agent.id, agent.persona_md, agent.description]);

  useEffect(() => {
    const m = (agent.metadata_json ?? {}) as Record<string, unknown>;
    setMaxToolRounds(
      String(
        readMetadataNumber(
          m,
          "max_tool_rounds",
          MAX_TOOL_ROUNDS_DEFAULT,
          MAX_TOOL_ROUNDS_MIN,
          MAX_TOOL_ROUNDS_MAX,
        ),
      ),
    );
    setMaxConcurrentTasks(
      String(
        readMetadataNumber(
          m,
          "max_concurrent_tasks",
          MAX_CONCURRENT_TASKS_DEFAULT,
          MAX_CONCURRENT_TASKS_MIN,
          MAX_CONCURRENT_TASKS_MAX,
        ),
      ),
    );
    setFallbackModel(readFallbackModel(m));
  }, [agent.id, agent.metadata_json]);

  useEffect(() => {
    if (!wantsEdit) return;
    const el = personaRef.current;
    if (el) {
      el.focus();
      const len = el.value.length;
      el.setSelectionRange(len, len);
    }
    const params = new URLSearchParams(searchParams.toString());
    params.delete("edit");
    params.set("tab", "overview");
    router.replace(`/agents/${agent.id}?${params.toString()}`);
  }, [wantsEdit, agent.id, router, searchParams]);

  const meta = (agent.metadata_json ?? {}) as Record<string, unknown>;
  const codeMode = Boolean(meta.code_mode);
  const requireApproval = Boolean(meta.approvals);

  const commitField = async (
    field: "description" | "persona_md",
    next: string,
    original: string,
  ) => {
    if (next === original || (next === "" && original === null)) return;
    setSavingField(field);
    try {
      await update.mutateAsync({ [field]: next });
      setSavedField(field);
      window.setTimeout(
        () => setSavedField((current) => (current === field ? null : current)),
        1500,
      );
    } catch {
      toast.error(tCommon("save") + " failed");
      if (field === "description") setDescription(original);
      if (field === "persona_md") setPersona(original);
    } finally {
      setSavingField(null);
    }
  };

  const commitMeta = async (
    field: string,
    patch: Record<string, unknown>,
  ): Promise<boolean> => {
    setSavingField(field);
    try {
      const next = { ...(agent.metadata_json ?? {}), ...patch };
      await update.mutateAsync({ metadata_json: next });
      setSavedField(field);
      window.setTimeout(
        () => setSavedField((c) => (c === field ? null : c)),
        1500,
      );
      return true;
    } catch {
      toast.error(tCommon("save") + " failed");
      return false;
    } finally {
      setSavingField(null);
    }
  };

  const onCommitToolRounds = async () => {
    const current = readMetadataNumber(
      (agent.metadata_json ?? {}) as Record<string, unknown>,
      "max_tool_rounds",
      MAX_TOOL_ROUNDS_DEFAULT,
      MAX_TOOL_ROUNDS_MIN,
      MAX_TOOL_ROUNDS_MAX,
    );
    const parsed = maxToolRounds.trim() === ""
      ? MAX_TOOL_ROUNDS_DEFAULT
      : Number(maxToolRounds);
    if (!Number.isFinite(parsed)) {
      setMaxToolRounds(String(current));
      return;
    }
    const clamped = Math.max(
      MAX_TOOL_ROUNDS_MIN,
      Math.min(MAX_TOOL_ROUNDS_MAX, Math.round(parsed)),
    );
    setMaxToolRounds(String(clamped));
    if (clamped === current) return;
    await commitMeta("max_tool_rounds", { max_tool_rounds: clamped });
  };

  const onCommitConcurrent = async () => {
    const current = readMetadataNumber(
      (agent.metadata_json ?? {}) as Record<string, unknown>,
      "max_concurrent_tasks",
      MAX_CONCURRENT_TASKS_DEFAULT,
      MAX_CONCURRENT_TASKS_MIN,
      MAX_CONCURRENT_TASKS_MAX,
    );
    const parsed = maxConcurrentTasks.trim() === ""
      ? MAX_CONCURRENT_TASKS_DEFAULT
      : Number(maxConcurrentTasks);
    if (!Number.isFinite(parsed)) {
      setMaxConcurrentTasks(String(current));
      return;
    }
    const clamped = Math.max(
      MAX_CONCURRENT_TASKS_MIN,
      Math.min(MAX_CONCURRENT_TASKS_MAX, Math.round(parsed)),
    );
    setMaxConcurrentTasks(String(clamped));
    if (clamped === current) return;
    await commitMeta("max_concurrent_tasks", { max_concurrent_tasks: clamped });
  };

  const onCommitFallback = async () => {
    const next = fallbackModel.trim();
    const current = readFallbackModel(
      (agent.metadata_json ?? {}) as Record<string, unknown>,
    );
    if (next === current) return;
    await commitMeta("fallback_model", { fallback_model: next ? next : null });
  };

  return (
    <div className="space-y-5">
      <section className="rounded-md border sh-card p-5">
        <div className="flex items-start gap-4">
          {agent.avatar_url ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={agent.avatar_url}
              alt=""
              className="size-14 shrink-0 rounded-lg object-cover"
            />
          ) : (
            <div className="flex size-14 shrink-0 items-center justify-center rounded-lg bg-[rgb(var(--color-primary)/0.1)] text-[rgb(var(--color-primary))]">
              <IconRobot className="size-7" />
            </div>
          )}
          <div className="min-w-0 flex-1 space-y-2">
            <InlineTextarea
              ref={descriptionRef}
              value={description}
              onChange={setDescription}
              onCommit={(next) =>
                commitField("description", next, agent.description ?? "")
              }
              placeholder={t("noDescription")}
              ariaLabel={t("noDescription")}
              fieldKey="description"
              savingField={savingField}
              savedField={savedField}
              className="text-[13px] sh-muted"
            />
            <div className="flex flex-wrap gap-1.5 pt-1">
              <Badge variant="outline">{agent.backend_kind}</Badge>
              <Badge variant="default">
                {agent.autonomy_level.toUpperCase()}
              </Badge>
              <Badge variant="default">{agent.visibility}</Badge>
              {codeMode && <Badge variant="primary">CodeMode</Badge>}
              {requireApproval && (
                <Badge variant="primary">{t("hitl")}</Badge>
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-md border sh-card p-5">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-base font-semibold">{t("personaTitle")}</h3>
          <FieldStatus
            saving={savingField === "persona_md"}
            saved={savedField === "persona_md"}
          />
        </div>
        <p className="mb-3 text-[12px] sh-muted">{t("personaDesc")}</p>
        <Textarea
          ref={personaRef}
          value={persona}
          onChange={(e) => setPersona(e.target.value)}
          onBlur={() => void commitField("persona_md", persona, agent.persona_md ?? "")}
          placeholder={t("personaEmpty")}
          rows={Math.max(8, Math.min(20, persona.split("\n").length + 2))}
          className="resize-y whitespace-pre-wrap rounded-md bg-black/5 font-mono text-[13px] dark:bg-white/5"
        />
      </section>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <CardBox title={t("runtimeTitle")}>
          <PickerRow label={t("backendLabel")}>
            <RuntimePicker agent={agent} />
          </PickerRow>
          <PickerRow label={t("defaultModelLabel")}>
            <DefaultModelPicker agent={agent} />
          </PickerRow>
          <PickerRow label={t("autonomyLabel")}>
            <AutonomyPicker agent={agent} />
          </PickerRow>
          <PickerRow label={t("visibilityLabel")}>
            <VisibilityPicker agent={agent} />
          </PickerRow>
          <PickerRow label={t("sandboxLabel")}>
            <SandboxPicker agent={agent} />
          </PickerRow>
        </CardBox>
        <CardBox title={t("idLabel")}>
          <Row
            label={t("createdLabel")}
            value={relativeTime(agent.created_at, locale)}
          />
          <Row
            label={t("updatedLabel")}
            value={relativeTime(agent.updated_at, locale)}
          />
          <Separator className="my-2" />
          <Row label={t("idLabel")} value={agent.id} mono />
        </CardBox>
      </section>

      <section className="rounded-md border sh-card p-5">
        <header className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold uppercase tracking-wide sh-muted">
              {t("advancedTitle")}
            </h3>
          </div>
          <FieldStatus
            saving={
              savingField === "max_tool_rounds" ||
              savingField === "max_concurrent_tasks" ||
              savingField === "fallback_model"
            }
            saved={
              savedField === "max_tool_rounds" ||
              savedField === "max_concurrent_tasks" ||
              savedField === "fallback_model"
            }
          />
        </header>
        <div className="grid gap-3 sm:grid-cols-3">
          <label className="space-y-1.5">
            <span className="block text-[11px] sh-muted">
              {t("maxToolRoundsLabel")}
            </span>
            <Input
              type="number"
              min={MAX_TOOL_ROUNDS_MIN}
              max={MAX_TOOL_ROUNDS_MAX}
              step="1"
              value={maxToolRounds}
              onChange={(e) => setMaxToolRounds(e.target.value)}
              onBlur={onCommitToolRounds}
            />
          </label>
          <label className="space-y-1.5">
            <span className="block text-[11px] sh-muted">
              {t("maxConcurrentTasksLabel")}
            </span>
            <Input
              type="number"
              min={MAX_CONCURRENT_TASKS_MIN}
              max={MAX_CONCURRENT_TASKS_MAX}
              step="1"
              value={maxConcurrentTasks}
              onChange={(e) => setMaxConcurrentTasks(e.target.value)}
              onBlur={onCommitConcurrent}
            />
          </label>
          <label className="space-y-1.5">
            <span className="block text-[11px] sh-muted">
              {t("fallbackModelLabel")}
            </span>
            <Input
              list={`fallback-model-options-${agent.id}`}
              value={fallbackModel}
              placeholder={t("fallbackModelPlaceholder")}
              onChange={(e) => setFallbackModel(e.target.value)}
              onBlur={onCommitFallback}
            />
            <datalist id={`fallback-model-options-${agent.id}`}>
              {(modelsQ.data?.options ?? []).map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.provider}/{opt.name}
                </option>
              ))}
            </datalist>
          </label>
        </div>
      </section>
    </div>
  );
}

function CardBox({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border sh-card p-5">
      <h3 className="mb-3 text-sm font-semibold uppercase tracking-wide sh-muted">
        {title}
      </h3>
      <div className="space-y-1.5 text-sm">{children}</div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  icon,
}: {
  label: string;
  value: string;
  mono?: boolean;
  icon?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <span className="shrink-0 text-[12px] sh-muted">{label}</span>
      <span
        className={`flex items-center gap-1 ${mono ? "truncate font-mono text-[11px]" : "truncate text-[13px]"}`}
        title={value}
      >
        {icon}
        {value}
      </span>
    </div>
  );
}

function PickerRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-[12px] sh-muted">{label}</span>
      {children}
    </div>
  );
}

function FieldStatus({ saving, saved }: { saving: boolean; saved: boolean }) {
  if (saving)
    return (
      <span className="inline-flex items-center gap-1 text-[11px] sh-muted">
        <IconLoader2 className="size-3 animate-spin" />
        Saving…
      </span>
    );
  if (saved)
    return (
      <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600">
        <IconCheck className="size-3" />
        Saved
      </span>
    );
  return null;
}

interface InlineTextareaProps {
  value: string;
  onChange: (v: string) => void;
  onCommit: (next: string) => void | Promise<void>;
  ariaLabel: string;
  placeholder?: string;
  fieldKey: string;
  savingField: string | null;
  savedField: string | null;
  className?: string;
}

const InlineTextarea = forwardRef<HTMLTextAreaElement, InlineTextareaProps>(
  function InlineTextarea(
    {
      value,
      onChange,
      onCommit,
      ariaLabel,
      placeholder,
      fieldKey,
      savingField,
      savedField,
      className,
    },
    ref,
  ) {
    return (
      <div className="flex items-start gap-2">
        <textarea
          ref={ref}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => void onCommit(value)}
          aria-label={ariaLabel}
          placeholder={placeholder}
          rows={2}
          className={`w-full resize-none rounded-sm bg-transparent px-1 outline-none transition-colors focus:bg-black/5 dark:focus:bg-white/5 ${className ?? ""}`}
        />
        <FieldStatus
          saving={savingField === fieldKey}
          saved={savedField === fieldKey}
        />
      </div>
    );
  },
);
