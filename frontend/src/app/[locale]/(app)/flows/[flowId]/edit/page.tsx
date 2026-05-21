"use client";

import { use, useMemo, useState } from "react";
import { IconLoader2, IconRoute2, IconScript } from "@tabler/icons-react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { FlowForm } from "@/components/flows/FlowForm";
import { FlowCanvas } from "@/components/flows/FlowCanvas";
import { NodePropertiesPanel } from "@/components/flows/NodePropertiesPanel";
import {
  blankGraph,
  type FlowGraphJson,
} from "@/components/flows/nodeTypes";
import { useFlow, useUpdateFlow } from "@/hooks/use-flows";
import { cn } from "@/lib/utils";

type Mode = "classic" | "visual";

export default function EditFlowPage({
  params,
}: {
  params: Promise<{ flowId: string }>;
}) {
  const { flowId } = use(params);
  const t = useTranslations("flows");
  const { data, isLoading } = useFlow(flowId);

  const initialMode: Mode = useMemo(() => {
    const g = (data?.graph_json ?? {}) as { nodes?: unknown[] };
    return Array.isArray(g.nodes) && g.nodes.length > 0 ? "visual" : "classic";
  }, [data?.id, data?.graph_json]);

  const [mode, setMode] = useState<Mode>(initialMode);
  // Re-sync mode when the flow first loads.
  useMemo(() => setMode(initialMode), [initialMode]);

  return (
    <div className="flex h-full flex-col p-6">
      <PageHeader
        title={t("edit")}
        description={data?.name}
        actions={
          <div className="flex items-center gap-1 rounded-md border p-0.5 text-sm">
            <ModeButton
              active={mode === "classic"}
              onClick={() => setMode("classic")}
              icon={<IconScript className="size-3.5" />}
              label={t("editor.classic")}
            />
            <ModeButton
              active={mode === "visual"}
              onClick={() => setMode("visual")}
              icon={<IconRoute2 className="size-3.5" />}
              label={t("editor.visual")}
            />
          </div>
        }
      />

      {isLoading && <Skeleton className="h-[420px]" />}
      {data && mode === "classic" && <FlowForm mode="edit" initial={data} />}
      {data && mode === "visual" && <VisualEditor flowId={flowId} />}
    </div>
  );
}

function ModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex items-center gap-1 rounded px-2.5 py-1 transition-colors",
        active
          ? "bg-black/5 font-medium dark:bg-white/10"
          : "hover:bg-black/5 dark:hover:bg-white/5",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

/** Visual DAG editor. Loads graph_json from the flow, edits locally, saves
 *  on demand via PATCH. */
function VisualEditor({ flowId }: { flowId: string }) {
  const t = useTranslations("flows.canvas");
  const tSettings = useTranslations("settings");
  const { data } = useFlow(flowId);
  const update = useUpdateFlow(flowId);

  const [graph, setGraph] = useState<FlowGraphJson>(() => {
    const g = (data?.graph_json ?? {}) as unknown as FlowGraphJson;
    if (Array.isArray(g.nodes) && g.nodes.length > 0) return g;
    return blankGraph();
  });
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const patchNode = (id: string, patch: Record<string, unknown>) => {
    setGraph((g) => ({
      ...g,
      nodes: (g.nodes ?? []).map((n) =>
        n.id === id ? { ...n, data: { ...(n.data ?? {}), ...patch } } : n,
      ),
    }));
  };

  const deleteNode = (id: string) => {
    setGraph((g) => ({
      nodes: (g.nodes ?? []).filter((n) => n.id !== id),
      edges: (g.edges ?? []).filter((e) => e.source !== id && e.target !== id),
    }));
    if (selectedId === id) setSelectedId(null);
  };

  const save = async () => {
    try {
      await update.mutateAsync({
        graph_json: graph as unknown as Record<string, unknown>,
      });
      toast.success(tSettings("saved"));
    } catch {
      toast.error(tSettings("saveFailed"));
    }
  };

  return (
    <div className="mt-2 flex flex-1 flex-col gap-3">
      <div className="grid flex-1 gap-3 lg:grid-cols-[200px_1fr_320px]">
        {/* Palette */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">{t("palette")}</CardTitle>
            <CardDescription className="text-[11px]">
              {t("paletteHint")}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5">
            <PaletteItem kind="agent_call" label="Agent" />
            <PaletteItem kind="http_request" label="HTTP" />
            <PaletteItem kind="end" label="End" />
          </CardContent>
        </Card>

        {/* Canvas */}
        <Card className="overflow-hidden p-0">
          <div className="h-[560px] w-full">
            <FlowCanvas
              graph={graph}
              onGraphChange={setGraph}
              selectedNodeId={selectedId}
              onSelectNode={setSelectedId}
            />
          </div>
        </Card>

        {/* Properties */}
        <Card className="overflow-hidden">
          <NodePropertiesPanel
            graph={graph}
            selectedNodeId={selectedId}
            onPatchNode={patchNode}
            onDeleteNode={deleteNode}
          />
        </Card>
      </div>

      <div className="flex items-center justify-end gap-2">
        <span className="text-[11px] sh-muted">
          {t("nodeCount", { n: graph.nodes.length })} ·{" "}
          {t("edgeCount", { n: graph.edges.length })}
        </span>
        <Button onClick={save} disabled={update.isPending}>
          {update.isPending && <IconLoader2 className="size-4 animate-spin" />}
          {t("save")}
        </Button>
      </div>
    </div>
  );
}

function PaletteItem({
  kind,
  label,
}: {
  kind: "agent_call" | "http_request" | "end";
  label: string;
}) {
  return (
    <button
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("application/sh-flow-node", kind);
        e.dataTransfer.effectAllowed = "move";
      }}
      className="flex w-full cursor-grab items-center gap-2 rounded-md border bg-black/3 px-2 py-1.5 text-left text-xs active:cursor-grabbing dark:bg-white/3"
    >
      <span className="inline-block size-2 rounded-full bg-[rgb(var(--color-primary))]" />
      {label}
    </button>
  );
}
