"use client";

import { memo } from "react";
import { Handle, type NodeProps, Position } from "@xyflow/react";
import {
  IconCheck,
  IconFlag,
  IconLoader2,
  IconPlayerPlay,
  IconRobot,
  IconRouter,
  IconX,
} from "@tabler/icons-react";

import type { FlowNodeType } from "../nodeTypes";
import { cn } from "@/lib/utils";

interface NodeMeta {
  label: string;
  color: string; // tailwind accent color classes
  icon: React.ComponentType<{ className?: string }>;
  hasInput: boolean;
  hasOutput: boolean;
  description: string;
}

export const NODE_TYPE_META: Record<FlowNodeType, NodeMeta> = {
  start: {
    label: "Start",
    color: "border-emerald-500 bg-emerald-50 dark:bg-emerald-950/30",
    icon: IconPlayerPlay,
    hasInput: false,
    hasOutput: true,
    description: "Trigger payload entry point",
  },
  agent_call: {
    label: "Agent",
    color: "border-[rgb(var(--color-primary))] bg-blue-50 dark:bg-blue-950/30",
    icon: IconRobot,
    hasInput: true,
    hasOutput: true,
    description: "Run an agent with a templated prompt",
  },
  http_request: {
    label: "HTTP",
    color: "border-purple-500 bg-purple-50 dark:bg-purple-950/30",
    icon: IconRouter,
    hasInput: true,
    hasOutput: true,
    description: "Call an external HTTP endpoint",
  },
  end: {
    label: "End",
    color: "border-amber-500 bg-amber-50 dark:bg-amber-950/30",
    icon: IconFlag,
    hasInput: true,
    hasOutput: false,
    description: "Materialize the final output",
  },
};

interface NodeData {
  label: string;
  nodeType: FlowNodeType;
  config: Record<string, unknown>;
  status?: "pending" | "running" | "success" | "failed";
}

function StatusDot({ status }: { status?: NodeData["status"] }) {
  if (!status) return null;
  if (status === "running") {
    return (
      <span
        className="inline-flex size-4 items-center justify-center rounded-full bg-amber-400 text-white"
        title="running"
      >
        <IconLoader2 className="size-3 animate-spin" />
      </span>
    );
  }
  if (status === "success") {
    return (
      <span
        className="inline-flex size-4 items-center justify-center rounded-full bg-emerald-500 text-white"
        title="success"
      >
        <IconCheck className="size-3" />
      </span>
    );
  }
  if (status === "failed") {
    return (
      <span
        className="inline-flex size-4 items-center justify-center rounded-full bg-red-500 text-white"
        title="failed"
      >
        <IconX className="size-3" />
      </span>
    );
  }
  // pending
  return (
    <span
      className="inline-block size-2.5 rounded-full bg-neutral-400"
      title="pending"
    />
  );
}

function _FlowNodeView({ data }: NodeProps) {
  const nodeData = data as unknown as NodeData;
  const meta =
    NODE_TYPE_META[nodeData.nodeType] ?? NODE_TYPE_META.agent_call;
  const Icon = meta.icon;

  // Pick a tiny summary based on node type + config.
  const summary = buildSummary(nodeData.nodeType, nodeData.config);

  return (
    <div
      className={cn(
        "sh-node min-w-[180px] max-w-[260px] rounded-lg border-2 p-2 shadow-sm transition-colors",
        meta.color,
      )}
    >
      {meta.hasInput && (
        <Handle type="target" position={Position.Left} className="!size-2.5" />
      )}

      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 shrink-0" />
        <span className="text-[11px] font-semibold uppercase tracking-wide">
          {meta.label}
        </span>
        <span className="ml-auto">
          <StatusDot status={nodeData.status} />
        </span>
      </div>
      {summary && (
        <div className="mt-1 line-clamp-2 break-words text-[11px] text-neutral-700 dark:text-neutral-300">
          {summary}
        </div>
      )}

      {meta.hasOutput && (
        <Handle
          type="source"
          position={Position.Right}
          className="!size-2.5"
        />
      )}
    </div>
  );
}

export const FlowNodeView = memo(_FlowNodeView);

function buildSummary(type: FlowNodeType, cfg: Record<string, unknown>): string {
  if (type === "start") return "trigger payload";
  if (type === "agent_call") {
    const prompt = String(cfg.prompt_template ?? "");
    return prompt ? prompt.slice(0, 80) : "(no prompt set)";
  }
  if (type === "http_request") {
    const method = String(cfg.method ?? "GET");
    const url = String(cfg.url ?? "");
    return url ? `${method} ${url.slice(0, 50)}` : "(no url set)";
  }
  if (type === "end") {
    const mode = String(cfg.output_mode ?? "flow_run");
    return `mode=${mode}`;
  }
  return "";
}
