"use client";

import { useTranslations } from "next-intl";
import { IconInfoCircle, IconTrash } from "@tabler/icons-react";

import { Badge } from "@/components/ui/badge";
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
import { Textarea } from "@/components/ui/textarea";
import { useAgents } from "@/hooks/use-agents";

import { NODE_TYPE_META } from "./nodes";
import type { FlowGraphJson, FlowNodeJson, FlowNodeType } from "./nodeTypes";

export function NodePropertiesPanel({
  graph,
  selectedNodeId,
  onPatchNode,
  onDeleteNode,
}: {
  graph: FlowGraphJson;
  selectedNodeId: string | null;
  onPatchNode: (id: string, patch: Record<string, unknown>) => void;
  onDeleteNode: (id: string) => void;
}) {
  const t = useTranslations("flows.canvas");
  const node = (graph.nodes ?? []).find((n) => n.id === selectedNodeId);

  if (!node) {
    return (
      <aside className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-xs sh-muted">
        <IconInfoCircle className="size-5" />
        <p>{t("selectHint")}</p>
      </aside>
    );
  }

  const meta = NODE_TYPE_META[node.type as FlowNodeType];

  return (
    <aside className="flex h-full flex-col gap-3 overflow-y-auto p-4">
      <header>
        <div className="flex items-center gap-2">
          <Badge variant="primary">{meta?.label ?? node.type}</Badge>
          <span className="font-mono text-[10px] sh-muted">{node.id}</span>
        </div>
        <p className="mt-1 text-[11px] sh-muted">{meta?.description}</p>
      </header>

      {node.type === "start" && <StartForm />}
      {node.type === "agent_call" && (
        <AgentCallForm node={node} onPatch={onPatchNode} />
      )}
      {node.type === "http_request" && (
        <HttpForm node={node} onPatch={onPatchNode} />
      )}
      {node.type === "end" && <EndForm node={node} onPatch={onPatchNode} />}

      {node.type !== "start" && (
        <Button
          variant="destructive"
          size="sm"
          className="mt-auto"
          onClick={() => onDeleteNode(node.id)}
        >
          <IconTrash className="size-3.5" />
          {t("deleteNode")}
        </Button>
      )}
    </aside>
  );
}

function StartForm() {
  const t = useTranslations("flows.canvas.nodes.start");
  return (
    <div className="rounded-md border border-dashed p-3 text-xs sh-muted">
      {t("hint")}
    </div>
  );
}

function AgentCallForm({
  node,
  onPatch,
}: {
  node: FlowNodeJson;
  onPatch: (id: string, patch: Record<string, unknown>) => void;
}) {
  const t = useTranslations("flows.canvas.nodes.agent");
  const { data: agents } = useAgents();
  const cfg = (node.data ?? {}) as Record<string, unknown>;

  return (
    <div className="space-y-3">
      <div className="grid gap-1.5">
        <Label>{t("agent")}</Label>
        <Select
          value={String(cfg.agent_id ?? "")}
          onValueChange={(v) => onPatch(node.id, { agent_id: v })}
        >
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

      <div className="grid gap-1.5">
        <Label>{t("prompt")}</Label>
        <Textarea
          value={String(cfg.prompt_template ?? "")}
          onChange={(e) =>
            onPatch(node.id, { prompt_template: e.target.value })
          }
          className="min-h-[140px] font-mono text-[12px]"
          placeholder={t("promptPlaceholder")}
        />
        <p className="text-[11px] sh-muted">{t("promptHint")}</p>
      </div>

      <div className="grid gap-1.5">
        <Label>{t("iterBudget")}</Label>
        <Input
          type="number"
          min={1}
          max={32}
          value={Number(cfg.iteration_budget ?? 8)}
          onChange={(e) =>
            onPatch(node.id, {
              iteration_budget: Math.max(
                1,
                Math.min(32, Number(e.target.value) || 8),
              ),
            })
          }
        />
      </div>
    </div>
  );
}

function HttpForm({
  node,
  onPatch,
}: {
  node: FlowNodeJson;
  onPatch: (id: string, patch: Record<string, unknown>) => void;
}) {
  const t = useTranslations("flows.canvas.nodes.http");
  const cfg = (node.data ?? {}) as Record<string, unknown>;
  const headers = (cfg.headers as Record<string, string>) ?? {};

  const setHeader = (k: string, v: string) => {
    const copy = { ...headers, [k]: v };
    onPatch(node.id, { headers: copy });
  };
  const removeHeader = (k: string) => {
    const { [k]: _, ...rest } = headers;
    onPatch(node.id, { headers: rest });
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-[110px_1fr] gap-2">
        <div className="grid gap-1.5">
          <Label>{t("method")}</Label>
          <Select
            value={String(cfg.method ?? "GET")}
            onValueChange={(v) => onPatch(node.id, { method: v })}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                <SelectItem key={m} value={m}>
                  {m}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="grid gap-1.5">
          <Label>{t("url")}</Label>
          <Input
            value={String(cfg.url ?? "")}
            onChange={(e) => onPatch(node.id, { url: e.target.value })}
            placeholder="https://…"
          />
        </div>
      </div>

      <div className="grid gap-1.5">
        <Label>{t("body")}</Label>
        <Textarea
          value={String(cfg.body ?? "")}
          onChange={(e) => onPatch(node.id, { body: e.target.value })}
          className="min-h-[100px] font-mono text-[12px]"
          placeholder={t("bodyPlaceholder")}
        />
        <p className="text-[11px] sh-muted">{t("bodyHint")}</p>
      </div>

      <div className="grid gap-1.5">
        <Label>{t("headers")}</Label>
        <div className="space-y-1">
          {Object.entries(headers).map(([k, v]) => (
            <div key={k} className="flex items-center gap-1">
              <Input
                value={k}
                onChange={(e) => {
                  removeHeader(k);
                  setHeader(e.target.value, String(v));
                }}
                placeholder="Header-Name"
                className="flex-1 font-mono text-[11px]"
              />
              <Input
                value={String(v)}
                onChange={(e) => setHeader(k, e.target.value)}
                placeholder="value"
                className="flex-1 font-mono text-[11px]"
              />
              <Button
                size="icon"
                variant="ghost"
                className="size-6"
                onClick={() => removeHeader(k)}
              >
                <IconTrash className="size-3" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setHeader("", "")}
          >
            + {t("addHeader")}
          </Button>
        </div>
      </div>

      <div className="grid gap-1.5">
        <Label>{t("timeout")}</Label>
        <Input
          type="number"
          min={1}
          max={60}
          value={Number(cfg.timeout ?? 10)}
          onChange={(e) =>
            onPatch(node.id, {
              timeout: Math.max(1, Math.min(60, Number(e.target.value) || 10)),
            })
          }
        />
      </div>
    </div>
  );
}

function EndForm({
  node,
  onPatch,
}: {
  node: FlowNodeJson;
  onPatch: (id: string, patch: Record<string, unknown>) => void;
}) {
  const t = useTranslations("flows.canvas.nodes.end");
  const cfg = (node.data ?? {}) as Record<string, unknown>;

  return (
    <div className="space-y-3">
      <div className="grid gap-1.5">
        <Label>{t("mode")}</Label>
        <Select
          value={String(cfg.output_mode ?? "flow_run")}
          onValueChange={(v) => onPatch(node.id, { output_mode: v })}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="flow_run">{t("modeFlowRun")}</SelectItem>
            <SelectItem value="session_message">
              {t("modeSessionMessage")}
            </SelectItem>
            <SelectItem value="noop">{t("modeNoop")}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="grid gap-1.5">
        <Label>{t("text")}</Label>
        <Textarea
          value={String(cfg.text ?? "")}
          onChange={(e) => onPatch(node.id, { text: e.target.value })}
          className="min-h-[100px] font-mono text-[12px]"
          placeholder="{{n_agent.text}}"
        />
        <p className="text-[11px] sh-muted">{t("textHint")}</p>
      </div>
    </div>
  );
}
