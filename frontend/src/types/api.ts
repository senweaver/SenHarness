/** Mirrors `backend/app/schemas/*.py` for the subset used by P0 UI. */

export interface MembershipBrief {
  workspace_id: string;
  workspace_name: string;
  workspace_slug: string;
  role: string;
  department_id?: string | null;
}

export interface MeOut {
  id: string;
  email: string;
  name: string;
  avatar_url?: string | null;
  status: string;
  platform_role: string;
  oauth_provider?: string | null;
  profile_json: Record<string, unknown>;
  workspaces: MembershipBrief[];
  current_workspace_id: string | null;
  current_role: string | null;
  current_department_id: string | null;
  permissions: string[];
  created_at: string;
  updated_at: string;
}

export interface DepartmentRead {
  id: string;
  workspace_id: string;
  parent_id: string | null;
  name: string;
  path: string;
  created_at: string;
  updated_at: string;
  member_count: number;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  expires_at: string;
}

export interface AgentRead {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  avatar_url: string | null;
  persona_md: string | null;
  backend_kind: "native" | "openclaw";
  backend_adapter_id: string | null;
  visibility: "private" | "workspace" | "public";
  autonomy_level: "l1" | "l2" | "l3";
  skill_refs_json: unknown[];
  memory_config_json: Record<string, unknown>;
  quotas_json: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface AgentRecent extends AgentRead {
  starred: boolean;
  pinned: boolean;
  last_message_at: string | null;
  message_count: number;
}

export interface SessionRead {
  id: string;
  workspace_id: string;
  kind: "p2p" | "squad" | "channel";
  subject_id: string | null;
  channel_id: string | null;
  owner_identity_id: string | null;
  title: string | null;
  state: "active" | "archived";
  summary_md: string | null;
  last_message_at: string | null;
  message_count: number;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRead {
  id: string;
  workspace_id: string;
  session_id: string;
  agent_id: string | null;
  run_id: string | null;
  tool_name: string;
  tool_args: Record<string, unknown>;
  summary: string | null;
  status: "pending" | "approved" | "denied" | "expired" | "cancelled";
  requested_by_identity_id: string | null;
  decided_by_identity_id: string | null;
  decided_reason: string | null;
  decided_at: string | null;
  expires_at: string | null;
  created_at: string;
  requester_department_name?: string | null;
  decided_by_department_name?: string | null;
}

export type SquadStrategy =
  | "planner"
  | "worker_pool"
  | "router"
  | "handoff"
  | "debate";

export interface SquadMemberRead {
  id: string;
  squad_id: string;
  agent_id: string;
  role_in_squad: string;
  weight: number;
  created_at: string;
  updated_at: string;
}

export interface SquadRead {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  strategy: SquadStrategy;
  config_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface SquadReadWithMembers extends SquadRead {
  members: SquadMemberRead[];
}

export interface AgentPublicCard extends AgentRead {
  stars: number;
}

export interface MessageRead {
  id: string;
  workspace_id: string;
  session_id: string;
  role: string;
  author_identity_id: string | null;
  author_agent_id: string | null;
  content_json: Record<string, unknown>;
  tool_call_json: Record<string, unknown> | null;
  tool_result_json: Record<string, unknown> | null;
  thinking_json: Record<string, unknown> | null;
  attachments_json: unknown[];
  token_usage_json: Record<string, unknown>;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

// ─── Message ratings (thumbs-up / thumbs-down) ─────────────
export type RatingValue = 1 | -1;

export interface MessageRatingRead {
  id: string;
  workspace_id: string;
  message_id: string;
  identity_id: string;
  rating: RatingValue;
  comment: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageRatingSummary {
  message_id: string;
  likes: number;
  dislikes: number;
  /** The current caller's vote, or null if not rated. */
  my_rating: RatingValue | null;
}

// ─── Session sharing ───────────────────────────────────────
export type SharePermission = "view" | "edit";
export type ShareVisibility = "private" | "workspace" | "public";

export interface SessionShareRead {
  id: string;
  session_id: string;
  token: string | null;
  permission: SharePermission;
  visibility: ShareVisibility;
  shared_by_identity_id: string | null;
  shared_with_identity_id: string | null;
  shared_with_email: string | null;
  shared_by_email: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SessionShareList {
  items: SessionShareRead[];
  total: number;
}

export interface PublicSessionMessage {
  role: string;
  content_json: Record<string, unknown>;
  tool_call_json: Record<string, unknown> | null;
  attachments_json: unknown[];
  created_at: string;
}

export interface PublicSharedSession {
  session_id: string;
  title: string | null;
  permission: SharePermission;
  expires_at: string | null;
  messages: PublicSessionMessage[];
}
