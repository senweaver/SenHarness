export type FlowNodeType = "start" | "agent_call" | "http_request" | "end";

export interface FlowNodeJson {
  id: string;
  type: FlowNodeType;
  position?: { x: number; y: number };
  data?: Record<string, unknown>;
}

export interface FlowEdgeJson {
  id: string;
  source: string;
  target: string;
}

export interface FlowGraphJson {
  nodes: FlowNodeJson[];
  edges: FlowEdgeJson[];
}

/** Returns a fresh blank graph with a single start node. */
export function blankGraph(): FlowGraphJson {
  return {
    nodes: [
      {
        id: "start",
        type: "start",
        position: { x: 80, y: 160 },
        data: {},
      },
    ],
    edges: [],
  };
}
