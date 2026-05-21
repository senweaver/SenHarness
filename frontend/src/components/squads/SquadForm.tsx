"use client";

import { useEffect, useState } from "react";
import { useRouter } from "@/lib/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import {
  IconGripVertical,
  IconLoader2,
  IconPlus,
  IconRobot,
  IconTrash,
} from "@tabler/icons-react";

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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAgents } from "@/hooks/use-agents";
import {
  type SquadCreateInput,
  type SquadMemberInput,
  useCreateSquad,
  useDeleteSquad,
  useReplaceSquadMembers,
  useUpdateSquad,
} from "@/hooks/use-squads";
import type {
  AgentRead,
  SquadReadWithMembers,
  SquadStrategy,
} from "@/types/api";

interface SquadFormProps {
  mode: "create" | "edit";
  initial?: SquadReadWithMembers;
}

interface MemberRow {
  agent_id: string;
  role_in_squad: string;
  weight: number;
}

export function SquadForm({ mode, initial }: SquadFormProps) {
  const t = useTranslations("settings.squads");
  const tCommon = useTranslations("common");
  const tSettings = useTranslations("settings");
  const router = useRouter();
  const { data: agents } = useAgents();

  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [strategy, setStrategy] = useState<SquadStrategy>(
    initial?.strategy ?? "router",
  );
  const [members, setMembers] = useState<MemberRow[]>(() =>
    (initial?.members ?? []).map((m) => ({
      agent_id: m.agent_id,
      role_in_squad: m.role_in_squad,
      weight: m.weight,
    })),
  );

  // Hooks must run unconditionally (rules-of-hooks). The "" sentinel is
  // a stable id we hand the mutation hooks when there's no initial squad
  // yet — the mutations are gated behind ``initial`` at call time so this
  // never fires a request for the empty id.
  const create = useCreateSquad();
  const update = useUpdateSquad(initial?.id ?? "");
  const replaceMembers = useReplaceSquadMembers(initial?.id ?? "");
  const remove = useDeleteSquad();

  useEffect(() => {
    if (!initial) return;
    setName(initial.name ?? "");
    setDescription(initial.description ?? "");
    setStrategy(initial.strategy);
    setMembers(
      (initial.members ?? []).map((m) => ({
        agent_id: m.agent_id,
        role_in_squad: m.role_in_squad,
        weight: m.weight,
      })),
    );
  }, [initial?.id]);

  const agentsById = (agents ?? []).reduce<Record<string, AgentRead>>(
    (acc, a) => {
      acc[a.id] = a;
      return acc;
    },
    {},
  );

  const availableAgents = (agents ?? []).filter(
    (a) => !members.some((m) => m.agent_id === a.id),
  );

  const addMember = (agentId: string) => {
    if (!agentId || members.some((m) => m.agent_id === agentId)) return;
    setMembers((m) => [
      ...m,
      { agent_id: agentId, role_in_squad: "member", weight: m.length },
    ]);
  };

  const removeMember = (agentId: string) => {
    setMembers((m) => m.filter((x) => x.agent_id !== agentId));
  };

  const updateMember = (
    agentId: string,
    patch: Partial<Omit<MemberRow, "agent_id">>,
  ) => {
    setMembers((m) =>
      m.map((x) => (x.agent_id === agentId ? { ...x, ...patch } : x)),
    );
  };

  const moveMember = (agentId: string, dir: -1 | 1) => {
    setMembers((m) => {
      const idx = m.findIndex((x) => x.agent_id === agentId);
      if (idx < 0) return m;
      const to = idx + dir;
      if (to < 0 || to >= m.length) return m;
      const copy = [...m];
      const a = copy[idx];
      const b = copy[to];
      if (!a || !b) return m;
      copy[idx] = b;
      copy[to] = a;
      return copy.map((x, i) => ({ ...x, weight: i }));
    });
  };

  const submitting =
    create.isPending ||
    update.isPending ||
    replaceMembers.isPending ||
    remove.isPending;

  const submit = async () => {
    const payload: SquadCreateInput = {
      name,
      description: description || null,
      strategy,
      members: members.map<SquadMemberInput>((m) => ({
        agent_id: m.agent_id,
        role_in_squad: m.role_in_squad || "member",
        weight: m.weight,
      })),
    };
    try {
      if (mode === "edit" && initial) {
        await update.mutateAsync({
          name: payload.name,
          description: payload.description,
          strategy: payload.strategy,
        });
        await replaceMembers.mutateAsync(payload.members ?? []);
        toast.success(tSettings("saved"));
      } else {
        const created = await create.mutateAsync(payload);
        toast.success(tSettings("created"));
        router.push(`/squads/${created.id}`);
      }
    } catch {
      toast.error(
        tSettings(mode === "edit" ? "saveFailed" : "createFailed"),
      );
    }
  };

  const onDelete = async () => {
    if (!initial) return;
    if (!confirm(t("confirmDelete"))) return;
    try {
      await remove.mutateAsync(initial.id);
      toast.success(tSettings("deleted"));
      router.push("/squads");
    } catch {
      toast.error(tSettings("deleteFailed"));
    }
  };

  const strategyDescription = (s: SquadStrategy) =>
    t(`form.strategyDesc.${s}`);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>{mode === "create" ? t("new") : t("edit")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field id="name" label={t("form.name")}>
              <Input
                id="name"
                data-testid="squad-form-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                placeholder={t("form.namePlaceholder")}
              />
            </Field>
            <Field id="strategy" label={t("form.strategy")}>
              <Select
                value={strategy}
                onValueChange={(v) => setStrategy(v as SquadStrategy)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="router">
                    {t("form.strategyName.router")}
                  </SelectItem>
                  <SelectItem value="planner">
                    {t("form.strategyName.planner")}
                  </SelectItem>
                  <SelectItem value="worker_pool">
                    {t("form.strategyName.worker_pool")}
                  </SelectItem>
                  <SelectItem value="handoff">
                    {t("form.strategyName.handoff")}
                  </SelectItem>
                  <SelectItem value="debate">
                    {t("form.strategyName.debate")}
                  </SelectItem>
                </SelectContent>
              </Select>
              <p className="text-[11px] sh-muted">
                {strategyDescription(strategy)}
              </p>
            </Field>
          </div>

          <Field id="description" label={t("form.description")}>
            <Input
              id="description"
              data-testid="squad-form-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder={t("form.descriptionPlaceholder")}
            />
          </Field>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">{t("members.title")}</CardTitle>
          <p className="text-xs sh-muted">{t("members.description")}</p>
        </CardHeader>
        <CardContent className="space-y-2">
          <MemberAdder
            placeholder={t("members.addPlaceholder")}
            options={availableAgents}
            onPick={addMember}
          />

          {members.length === 0 ? (
            <div className="rounded-md border border-dashed py-6 text-center text-xs sh-muted">
              {t("members.empty")}
            </div>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {members.map((m, idx) => {
                const a = agentsById[m.agent_id];
                return (
                  <li
                    key={m.agent_id}
                    className="flex items-center gap-2 rounded-md border p-2"
                  >
                    <div className="flex shrink-0 flex-col">
                      <button
                        className="size-4 rounded hover:bg-black/5 disabled:opacity-30 dark:hover:bg-white/10"
                        disabled={idx === 0}
                        onClick={() => moveMember(m.agent_id, -1)}
                        title={t("members.moveUp")}
                        aria-label={t("members.moveUp")}
                        type="button"
                      >
                        <IconGripVertical className="size-3 rotate-180" />
                      </button>
                      <button
                        className="size-4 rounded hover:bg-black/5 disabled:opacity-30 dark:hover:bg-white/10"
                        disabled={idx === members.length - 1}
                        onClick={() => moveMember(m.agent_id, 1)}
                        title={t("members.moveDown")}
                        aria-label={t("members.moveDown")}
                        type="button"
                      >
                        <IconGripVertical className="size-3" />
                      </button>
                    </div>
                    {a?.avatar_url ? (
                      <img
                        src={a.avatar_url}
                        alt=""
                        className="size-7 shrink-0 rounded-full object-cover"
                      />
                    ) : (
                      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-black/10 dark:bg-white/10">
                        <IconRobot className="size-4" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">
                        {a?.name ?? m.agent_id}
                      </div>
                      {a?.description && (
                        <div className="truncate text-[11px] sh-muted">
                          {a.description}
                        </div>
                      )}
                    </div>
                    <Input
                      aria-label={t("members.roleLabel")}
                      value={m.role_in_squad}
                      onChange={(e) =>
                        updateMember(m.agent_id, {
                          role_in_squad: e.target.value,
                        })
                      }
                      placeholder={t("members.rolePlaceholder")}
                      className="w-[160px] text-xs"
                    />
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7"
                      onClick={() => removeMember(m.agent_id)}
                      title={tCommon("delete")}
                      aria-label={tCommon("delete")}
                    >
                      <IconTrash className="size-3.5" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        {mode === "edit" && initial ? (
          <Button
            variant="destructive"
            onClick={onDelete}
            disabled={submitting}
          >
            <IconTrash className="size-4" />
            {tCommon("delete")}
          </Button>
        ) : (
          <span />
        )}
        <div className="flex gap-2">
          <Button
            variant="ghost"
            onClick={() => router.back()}
            disabled={submitting}
          >
            {tCommon("cancel")}
          </Button>
          <Button
            data-testid="squad-form-submit"
            onClick={submit}
            disabled={submitting || !name.trim() || members.length === 0}
          >
            {submitting && <IconLoader2 className="size-4 animate-spin" />}
            {tCommon("save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function MemberAdder({
  options,
  onPick,
  placeholder,
}: {
  options: AgentRead[];
  onPick: (id: string) => void;
  placeholder: string;
}) {
  const [value, setValue] = useState<string>("");
  return (
    <div className="flex items-center gap-2">
      <Select
        value={value}
        onValueChange={(v) => {
          setValue(v);
          onPick(v);
          setValue("");
        }}
      >
        <SelectTrigger className="w-[240px]">
          <SelectValue placeholder={placeholder} />
        </SelectTrigger>
        <SelectContent>
          {options.length === 0 ? (
            <div className="px-2 py-1.5 text-xs sh-muted">(empty)</div>
          ) : (
            options.map((a) => (
              <SelectItem key={a.id} value={a.id}>
                {a.name}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      <IconPlus className="size-4 sh-muted" />
    </div>
  );
}

function Field({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}
