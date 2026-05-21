"use client";

import { use, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  type Edge,
  type Node,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { IconArrowLeft } from "@tabler/icons-react";
import { useTranslations } from "next-intl";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  SKILL_GRAPH_MAX_DEPTH,
  useSkillGraph,
} from "@/hooks/use-skill-graph";
import { Link, useRouter } from "@/lib/navigation";
import type {
  SkillGraphEdge,
  SkillGraphNode,
  SkillLineageEdgeKind,
} from "@/types/api";

interface GraphPageProps {
  params: Promise<{ packId: string }>;
}

const EDGE_KIND_COLOR: Record<SkillLineageEdgeKind, string> = {
  derived_from: "rgb(59 130 246)",
  supersedes: "rgb(217 70 239)",
  forked_from: "rgb(245 158 11)",
  pulled_from_hub: "rgb(16 185 129)",
};

function nodeBackgroundFor(node: SkillGraphNode): string {
  if (node.is_focus) return "rgb(var(--color-primary) / 0.18)";
  if (node.is_external) return "rgb(16 185 129 / 0.12)";
  return "rgb(var(--color-card) / 1)";
}

function nodeBorderFor(node: SkillGraphNode): string {
  if (node.is_focus) return "rgb(var(--color-primary))";
  if (node.is_external) return "rgb(16 185 129)";
  return "rgb(var(--color-border))";
}

interface NodeData extends Record<string, unknown> {
  raw: SkillGraphNode;
  onSelect: (packId: string) => void;
}

function laidOutNodes(rawNodes: SkillGraphNode[]): Node<NodeData>[] {
  // Naive radial layout: focus in the centre, every other node placed
  // around a circle. The xyflow controls let users zoom/pan/rearrange,
  // so a deterministic non-overlapping layout is enough for v1.
  const focus = rawNodes.find((n) => n.is_focus);
  const others = rawNodes.filter((n) => !n.is_focus);
  const radius = Math.max(220, 120 + others.length * 22);
  const center = { x: 0, y: 0 };
  const positioned: Node<NodeData>[] = [];
  if (focus) {
    positioned.push({
      id: focus.node_id,
      position: center,
      data: {
        raw: focus,
        onSelect: () => undefined,
      },
      type: "default",
    });
  }
  others.forEach((node, idx) => {
    const angle = (idx / Math.max(others.length, 1)) * Math.PI * 2;
    positioned.push({
      id: node.node_id,
      position: {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
      },
      data: {
        raw: node,
        onSelect: () => undefined,
      },
      type: "default",
    });
  });
  return positioned;
}

function styledEdges(rawEdges: SkillGraphEdge[]): Edge[] {
  return rawEdges.map((edge, idx) => ({
    id: `${edge.parent_id}__${edge.child_id}__${edge.kind}__${idx}`,
    source: edge.parent_id,
    target: edge.child_id,
    label: edge.kind,
    animated: edge.kind === "derived_from",
    style: { stroke: EDGE_KIND_COLOR[edge.kind] },
    labelStyle: {
      fontSize: 10,
      fill: EDGE_KIND_COLOR[edge.kind],
    },
  }));
}

function GraphCanvas({
  nodes: rawNodes,
  edges: rawEdges,
  onNodeOpen,
}: {
  nodes: SkillGraphNode[];
  edges: SkillGraphEdge[];
  onNodeOpen: (packId: string) => void;
}) {
  const t = useTranslations("skillGraph");
  const initialNodes = useMemo(() => laidOutNodes(rawNodes), [rawNodes]);
  const initialEdges = useMemo(() => styledEdges(rawEdges), [rawEdges]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>(
    initialNodes,
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges);

  useEffect(() => {
    setNodes(laidOutNodes(rawNodes));
  }, [rawNodes, setNodes]);
  useEffect(() => {
    setEdges(styledEdges(rawEdges));
  }, [rawEdges, setEdges]);

  return (
    <ReactFlow
      nodes={nodes.map((n) => {
        const node = n.data.raw;
        const label = node.is_external
          ? `${t("externalHubNodeLabel", { slug: node.slug })}`
          : node.name;
        return {
          ...n,
          data: {
            ...n.data,
            label,
          },
          // Inline style for the default node body — deliberately
          // ad-hoc since we only use the default node type here.
          style: {
            background: nodeBackgroundFor(node),
            border: `1px solid ${nodeBorderFor(node)}`,
            borderRadius: 12,
            padding: "8px 12px",
            fontSize: 12,
            maxWidth: 200,
            cursor: node.is_external ? "default" : "pointer",
          },
        } as Node<NodeData>;
      })}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={(_, node) => {
        const raw = node.data?.raw as SkillGraphNode | undefined;
        if (raw && raw.pack_id) onNodeOpen(raw.pack_id);
      }}
      fitView
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
      <Controls position="bottom-right" />
    </ReactFlow>
  );
}

export default function SkillGraphPage({ params }: GraphPageProps) {
  const { packId } = use(params);
  const t = useTranslations("skillGraph");
  const router = useRouter();
  const [depth, setDepth] = useState(2);
  const { data, isLoading, error } = useSkillGraph(packId, depth);

  const onNodeOpen = (id: string) => {
    if (id === packId) return;
    router.push(`/skills/${id}/graph`);
  };

  return (
    <div className="flex h-[calc(100vh-72px)] flex-col p-6">
      <PageHeader
        title={t("pageTitle")}
        description={t("description")}
        actions={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => router.push(`/skills`)}
            >
              <IconArrowLeft className="size-4" />
              {t("backToSkills")}
            </Button>
            <Select
              value={String(depth)}
              onValueChange={(v) => setDepth(Number(v))}
            >
              <SelectTrigger className="w-[140px]">
                <SelectValue placeholder={t("depthSelector")} />
              </SelectTrigger>
              <SelectContent>
                {Array.from({ length: SKILL_GRAPH_MAX_DEPTH }, (_, i) => i + 1).map(
                  (n) => (
                    <SelectItem key={n} value={String(n)}>
                      {t("depthHopLabel", { count: n })}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
          </div>
        }
      />

      <div className="flex flex-wrap items-center gap-2 pb-3 text-xs">
        <span className="sh-muted">{t("legendLabel")}</span>
        {(
          [
            "derived_from",
            "supersedes",
            "forked_from",
            "pulled_from_hub",
          ] as SkillLineageEdgeKind[]
        ).map((kind) => (
          <Badge
            key={kind}
            variant="outline"
            style={{ borderColor: EDGE_KIND_COLOR[kind], color: EDGE_KIND_COLOR[kind] }}
          >
            {t(`edgeKind_${kind}`)}
          </Badge>
        ))}
        {data?.truncated ? (
          <Badge variant="destructive" className="ml-2">
            {t("truncatedNotice")}
          </Badge>
        ) : null}
      </div>

      <div className="flex-1 overflow-hidden rounded-lg border">
        {isLoading ? (
          <Skeleton className="h-full" />
        ) : error ? (
          <div className="flex h-full items-center justify-center text-sm sh-muted">
            {t("loadError")}
          </div>
        ) : !data || data.nodes.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3">
            <p className="text-sm sh-muted">{t("emptyState")}</p>
            <Link href="/skills" className="text-xs underline">
              {t("backToSkills")}
            </Link>
          </div>
        ) : (
          <ReactFlowProvider>
            <GraphCanvas
              nodes={data.nodes}
              edges={data.edges}
              onNodeOpen={onNodeOpen}
            />
          </ReactFlowProvider>
        )}
      </div>

      <p className="pt-2 text-[11px] sh-muted">{t("clickNodeHint")}</p>
    </div>
  );
}
