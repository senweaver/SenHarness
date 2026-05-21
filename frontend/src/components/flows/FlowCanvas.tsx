"use client";

import { useCallback, useEffect, useMemo } from "react";
import {
  addEdge,
  Background,
  BackgroundVariant,
  type Connection,
  type Edge,
  type EdgeChange,
  MiniMap,
  type Node,
  type NodeChange,
  type NodeTypes,
  ReactFlow,
  ReactFlowProvider,
  useEdgesState,
  useNodesState,
  useReactFlow,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { FlowNodeView, NODE_TYPE_META } from "./nodes";
import type {
  FlowEdgeJson,
  FlowGraphJson,
  FlowNodeJson,
  FlowNodeType,
} from "./nodeTypes";

/** React Flow node/edge data helpers. */
type CanvasNode = Node<{
  label: string;
  nodeType: FlowNodeType;
  config: Record<string, unknown>;
  status?: "pending" | "running" | "success" | "failed";
}>;

const DEFAULT_POSITION = { x: 160, y: 120 };

export interface FlowCanvasProps {
  graph: FlowGraphJson;
  onGraphChange: (graph: FlowGraphJson) => void;
  selectedNodeId: string | null;
  onSelectNode: (id: string | null) => void;
  /** Optional map of ``node_id -> status`` for live run visualization. */
  nodeStatus?: Record<string, "pending" | "running" | "success" | "failed">;
  readOnly?: boolean;
}

/** Thin wrapper that owns a ReactFlowProvider so we can use hooks. */
export function FlowCanvas(props: FlowCanvasProps) {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}

function FlowCanvasInner({
  graph,
  onGraphChange,
  selectedNodeId,
  onSelectNode,
  nodeStatus,
  readOnly,
}: FlowCanvasProps) {
  const { screenToFlowPosition } = useReactFlow();

  // Hydrate React Flow state from the graph JSON prop on mount + whenever
  // the caller replaces the graph wholesale (e.g. loading a flow). Local
  // drag/connect edits don't bounce through the parent, to keep interactions
  // snappy; we push back to onGraphChange only when a node is added/removed
  // or connections change.
  const initialNodes = useMemo<CanvasNode[]>(
    () =>
      (graph.nodes ?? []).map((n) => ({
        id: n.id,
        type: "sh_node",
        position: n.position ?? DEFAULT_POSITION,
        data: {
          label: NODE_TYPE_META[n.type]?.label ?? n.type,
          nodeType: n.type,
          config: n.data ?? {},
        },
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const initialEdges = useMemo<Edge[]>(
    () =>
      (graph.edges ?? []).map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
      })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasNode>(
    initialNodes as CanvasNode[],
  );
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(
    initialEdges as Edge[],
  );

  // Apply inbound node-status updates (live run viz).
  useEffect(() => {
    if (!nodeStatus) return;
    setNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: { ...n.data, status: nodeStatus[n.id] },
      })),
    );
  }, [nodeStatus, setNodes]);

  // Push the current graph back up whenever nodes / edges structure changes.
  const pushUp = useCallback(
    (nextNodes: CanvasNode[], nextEdges: Edge[]) => {
      const outNodes: FlowNodeJson[] = nextNodes.map((n) => ({
        id: n.id,
        type: n.data.nodeType,
        position: n.position,
        data: n.data.config,
      }));
      const outEdges: FlowEdgeJson[] = nextEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
      }));
      onGraphChange({ nodes: outNodes, edges: outEdges });
    },
    [onGraphChange],
  );

  const handleNodesChange = useCallback(
    (changes: NodeChange<CanvasNode>[]) => {
      onNodesChange(changes);
      // For position + remove, bubble up on the next tick so the state has
      // settled. Add gets pushed via addNode().
      const structural = changes.some(
        (c) => c.type === "position" || c.type === "remove",
      );
      if (structural) {
        setTimeout(() => {
          setNodes((curN) =>
            setEdges((curE) => {
              pushUp(curN, curE);
              return curE;
            }) as never ?? curN,
          );
        }, 0);
      }
    },
    [onNodesChange, pushUp, setNodes, setEdges],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<Edge>[]) => {
      onEdgesChange(changes);
      if (changes.some((c) => c.type === "remove" || c.type === "add")) {
        setTimeout(() => {
          setEdges((curE) => {
            setNodes((curN) => {
              pushUp(curN, curE);
              return curN;
            });
            return curE;
          });
        }, 0);
      }
    },
    [onEdgesChange, pushUp, setNodes, setEdges],
  );

  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) => {
        const next = addEdge(params, eds);
        setNodes((curN) => {
          pushUp(curN, next);
          return curN;
        });
        return next;
      });
    },
    [setEdges, setNodes, pushUp],
  );

  // Drag-from-palette handling.
  const onDragOver = useCallback((event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const type = event.dataTransfer.getData(
        "application/sh-flow-node",
      ) as FlowNodeType | "";
      if (!type || !(type in NODE_TYPE_META)) return;
      const position = screenToFlowPosition({
        x: event.clientX,
        y: event.clientY,
      });
      const id = `${type}_${Math.random().toString(36).slice(2, 8)}`;
      const newNode: CanvasNode = {
        id,
        type: "sh_node",
        position,
        data: {
          label: NODE_TYPE_META[type].label,
          nodeType: type,
          config: {},
        },
      };
      setNodes((curN) => {
        const next = [...curN, newNode];
        setEdges((curE) => {
          pushUp(next, curE);
          return curE;
        });
        return next;
      });
      onSelectNode(id);
    },
    [screenToFlowPosition, setNodes, setEdges, pushUp, onSelectNode],
  );

  const nodeTypes: NodeTypes = useMemo(
    () => ({ sh_node: FlowNodeView }),
    [],
  );

  return (
    <div
      className="sh-flow-canvas relative h-full w-full"
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_e, n) => onSelectNode(n.id)}
        onPaneClick={() => onSelectNode(null)}
        nodesDraggable={!readOnly}
        nodesConnectable={!readOnly}
        elementsSelectable={!readOnly}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
      >
        <MiniMap
          zoomable
          pannable
          className="!bg-transparent"
          nodeColor={(n) => {
            const st = (n.data as { status?: string }).status;
            if (st === "running") return "#f59e0b";
            if (st === "success") return "#22c55e";
            if (st === "failed") return "#ef4444";
            return "#64748b";
          }}
        />
        <Background
          variant={BackgroundVariant.Dots}
          gap={20}
          size={1}
          className="opacity-50"
        />
      </ReactFlow>

      {/* Inline class ensures selected node glow matches the app's primary color. */}
      <style>{`
        .sh-flow-canvas .react-flow__node.selected .sh-node {
          outline: 2px solid rgb(var(--color-primary));
          outline-offset: 2px;
        }
      `}</style>
    </div>
  );
}
