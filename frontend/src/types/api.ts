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
  onboarded_at: string | null;
  workspaces: MembershipBrief[];
  current_workspace_id: string | null;
  current_role: string | null;
  current_department_id: string | null;
  permissions: string[];
  preferred_locale: string | null;
  created_at: string;
  updated_at: string;
}

export interface PublicBootstrap {
  site_name: string;
  primary_color_hex: string;
  default_locale: string;
  default_timezone: string;
  registration_mode: string;
}

export type SidebarItemType = "agent" | "squad" | "session";

export interface SidebarItem {
  type: SidebarItemType;
  id: string;
  name: string;
  avatar_seed: string;
  pinned: boolean;
  unread_count: number;
  last_activity_at: string | null;
  href: string;
}

export interface SidebarItemsResponse {
  items: SidebarItem[];
  total: number;
}

export interface OnboardingCompleteOut {
  onboarded_at: string;
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

export interface RegistrationModeOut {
  mode: "open_personal" | "open_invite_only" | "closed";
  invitation_required: boolean;
  requires_email_verification: boolean;
}

export interface RegistrationWorkspace {
  id: string;
  name: string;
  slug: string;
}

export interface RegistrationTokenPair {
  access_token: string;
  refresh_token: string;
  expires_at: string;
  refresh_expires_at: string;
  token_type: string;
}

export interface RegistrationResponse {
  identity_id: string;
  email: string;
  name: string;
  status: "pending" | "active" | "suspended";
  workspace: RegistrationWorkspace | null;
  workspace_slug_warning: boolean;
  auto_login_tokens: RegistrationTokenPair | null;
  requires_email_verification: boolean;
  registration_mode: "open_personal" | "open_invite_only" | "closed";
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
  default_model: string | null;
  default_search_provider_kind: string | null;
  served_model_name?: string | null;
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

export type ApprovalResourceType =
  | "skill_pack_archive"
  | "skill_pack_create"
  | "skill_pack_patch"
  | "skill_pack_edit"
  | "skill_pack_delete"
  | "skill_pack_write_file"
  | "skill_pack_remove_file"
  | "flow_create"
  | "hub_promotion"
  | "subagent_hallucination_review";

export interface ApprovalRead {
  id: string;
  workspace_id: string;
  // Nullable since M1.4 — non-tool approvals (Curator, evolver verbs,
  // M2.8 cron flow) carry no chat session.
  session_id: string | null;
  agent_id: string | null;
  run_id: string | null;
  tool_name: string;
  tool_args: Record<string, unknown>;
  summary: string | null;
  status: "pending" | "approved" | "denied" | "expired" | "cancelled";
  resource_type?: ApprovalResourceType | string | null;
  resource_id?: string | null;
  requested_by_identity_id: string | null;
  decided_by_identity_id: string | null;
  decided_reason: string | null;
  decided_at: string | null;
  expires_at: string | null;
  reminder_sent?: boolean;
  created_at: string;
  requester_department_name?: string | null;
  decided_by_department_name?: string | null;
}

export interface ApprovalDispatchResultRead {
  approval_id: string;
  resource_type: string;
  resource_id: string | null;
  applied_object_id: string | null;
  audit_action: string;
}

export interface ApprovalDecisionResponse {
  approval: ApprovalRead;
  dispatch_result: ApprovalDispatchResultRead | null;
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
  /** Sidebar slug from the backend catalog (e.g. "engineering"). Null for user-published public agents that don't carry the template metadata. */
  category: string | null;
  /** Curated tag list from the catalog plus any auto-derived tags. */
  tags: string[];
}

export interface AgentCategory {
  slug: string;
  name_cn: string;
  name_en: string;
  count: number;
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

// ─── Session goal lock (M0.1) ──────────────────────────────
export interface SessionGoalRead {
  id: string;
  workspace_id: string;
  session_id: string;
  goal_text: string;
  success_criteria: string[];
  locked_by: string;
  locked_at: string;
  unlocked_at: string | null;
  unlocked_by: string | null;
  alignment_threshold: number;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SessionGoalCreate {
  goal_text: string;
  success_criteria?: string[];
  alignment_threshold?: number;
  metadata_json?: Record<string, unknown>;
}

export interface SessionGoalUpdate {
  goal_text?: string;
  success_criteria?: string[];
  alignment_threshold?: number;
  metadata_json?: Record<string, unknown>;
}

export interface GoalAlignmentScoreRead {
  id: string;
  workspace_id: string;
  session_goal_id: string;
  message_id: string;
  score: number;
  rationale: string | null;
  judged_by_model: string | null;
  flagged: boolean;
  created_at: string;
  updated_at: string;
}

export type ArtifactTurnRole = "user" | "assistant" | "tool" | "system";

export interface ArtifactTurnReadDto {
  role: ArtifactTurnRole;
  text: string | null;
  tool_calls: Array<Record<string, unknown>>;
  tool_results: Array<Record<string, unknown>>;
  thinking: string | null;
  iteration: number;
  message_id: string | null;
  timestamp: string | null;
}

export interface SessionArtifactRead {
  id: string;
  workspace_id: string;
  run_id: string;
  session_id: string;
  agent_id: string | null;
  identity_id: string | null;
  user_text_hash: string;
  turns_json: ArtifactTurnReadDto[];
  injected_skill_pack_ids: string[];
  invoked_tools: string[];
  iteration_count: number;
  final_outcome: string;
  error_kind: string | null;
  judge_score: number | null;
  goal_alignment_avg: number | null;
  finished_at: string;
  created_at: string;
  updated_at: string;
}

export interface JudgeVerdictRead {
  id: string;
  workspace_id: string;
  artifact_id: string;
  score: number;
  confidence: number;
  rationale: string;
  process_notes_json: string[];
  error_kind_hint: string | null;
  judged_by_model: string | null;
  latency_ms: number | null;
  degraded: boolean;
  created_at: string;
  updated_at: string;
}

export interface JudgeSessionSummary {
  session_id: string;
  total_artifacts: number;
  success: number;
  partial: number;
  failure: number;
  unjudged: number;
  degraded: number;
}

// ─── M0.7 Cache-aware mutation queue ──────────────────────────
export type PendingMemoryStatus =
  | "pending"
  | "promoted"
  | "skipped"
  | "failed";

export type PendingMemoryTargetTable = "memories" | "skill_packs";

export interface PendingMemoryRead {
  id: string;
  workspace_id: string;
  session_id: string;
  identity_id: string | null;
  target_table: PendingMemoryTargetTable;
  payload: Record<string, unknown>;
  status: PendingMemoryStatus;
  promoted_at: string | null;
  promoted_target_id: string | null;
  failure_reason: string | null;
  failure_count: number;
  created_at: string;
  updated_at: string;
}

export interface PendingMemoryStats {
  workspace_id: string;
  pending: number;
  promoted: number;
  skipped: number;
  failed: number;
  oldest_pending_at: string | null;
}

export interface PromoteSweepResult {
  workspaces_visited: number;
  promoted: number;
  skipped: number;
  failed: number;
}

// ─── M0.12 Workspace creation quota ───────────────────────────
export type WorkspaceCreationKind =
  | "self_register"
  | "oauth_register"
  | "manual"
  | "invitation_redeem"
  | "admin_provision";

export interface WorkspaceQuota {
  used: number;
  limit: number;
  remaining: number;
  creation_kind_allowed: boolean;
  rate_window_used: number;
  rate_window_limit: number;
  rate_window_seconds: number;
  source_kind: WorkspaceCreationKind;
  override_active: boolean;
  grandfathered: boolean;
}

export interface AdminWorkspaceQuotaRow {
  identity_id: string;
  email: string;
  name: string;
  status: "pending" | "active" | "suspended";
  platform_role: "user" | "platform_admin";
  source_kind: WorkspaceCreationKind;
  used: number;
  limit: number;
  override: number | null;
}

export interface AdminWorkspaceQuotaList {
  rows: AdminWorkspaceQuotaRow[];
  total: number;
}

export interface IdentityWorkspaceQuotaUpdate {
  identity_id: string;
  workspace_quota_override: number | null;
}

export type NotificationLevel = "info" | "success" | "warning" | "error";

export interface NotificationRead {
  id: string;
  workspace_id: string;
  recipient_identity_id: string;
  actor_identity_id: string | null;
  kind: string;
  level: NotificationLevel;
  title: string;
  body: string | null;
  resource_type: string | null;
  resource_id: string | null;
  action_url: string | null;
  metadata_json: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface UnreadNotificationCount {
  unread: number;
}

export interface NotificationEventDescriptor {
  key: string;
  title_key: string;
  message_key: string;
  default_channels: string[];
  default_urgency: "info" | "warn" | "critical";
  cooldown_seconds: number;
  target_audience: string;
  requires_email: boolean;
}

export interface NotificationPrefEntry {
  channels: string[];
  muted: boolean;
}

export interface NotificationGlobalPref {
  muted_until: string | null;
}

export interface NotificationPrefsRead {
  prefs: Record<string, NotificationPrefEntry>;
  _global: NotificationGlobalPref;
  catalog: NotificationEventDescriptor[];
}

export interface NotificationPrefsUpdate {
  prefs: Record<string, NotificationPrefEntry>;
  _global: NotificationGlobalPref;
}

// ── M0.13 unified platform settings ─────────────────────────
export interface PlatformSettingSection {
  section: string;
  value: Record<string, unknown>;
  env_overrides: string[];
  db_present: boolean;
  last_modified_at: string | null;
  dangerous_fields: string[];
  is_email_notify: boolean;
}

export interface PlatformSettingsListOut {
  sections: PlatformSettingSection[];
}

export interface PlatformSettingsResetOut {
  section: string;
  value: Record<string, unknown>;
}

export interface PlatformSettingsSchema {
  title?: string;
  description?: string;
  type: "object";
  properties: Record<string, PlatformSettingsField>;
  required?: string[];
  $defs?: Record<string, unknown>;
}

export interface PlatformSettingsField {
  type?: string | string[];
  title?: string;
  description?: string;
  default?: unknown;
  enum?: (string | number)[];
  format?: string;
  pattern?: string;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  items?: PlatformSettingsField;
  properties?: Record<string, PlatformSettingsField>;
  $ref?: string;
  anyOf?: PlatformSettingsField[];
}

export interface PlatformSmtpTestIn {
  host: string;
  port: number;
  username?: string | null;
  password?: string | null;
  from_address: string;
  use_tls: boolean;
  to?: string | null;
}

export interface PlatformSmtpTestOut {
  ok: boolean;
  transport: string;
  message_id?: string | null;
  error?: string | null;
}

export interface PlatformOAuthTestOut {
  ok: boolean;
  provider: string;
  metadata_url?: string | null;
  error?: string | null;
}

// ─── M1.10 — Skill diff renderer ──────────────────────────────
export interface SkillDiffRequest {
  old_content: string;
  new_content: string;
  context_lines?: number;
  file_label?: string;
  from_label?: string;
  to_label?: string;
}

export interface SkillDiffStats {
  added_lines: number;
  removed_lines: number;
  hunks: number;
}

export interface SkillDiffResponse {
  diff: string;
  stats: SkillDiffStats;
  files_changed: string[];
  truncated: boolean;
}

// ─── M1.2 — SkillPackVersion ──────────────────────────────────
export type SkillPackVersionState =
  | "proposed"
  | "validating"
  | "accepted"
  | "active"
  | "retired"
  | "rejected";

export interface SkillPackVersionRead {
  id: string;
  workspace_id: string;
  pack_id: string;
  version_no: number;
  content_hash: string;
  state: SkillPackVersionState;
  created_by: string;
  creator_identity_id: string | null;
  source_run_ids: string[];
  judge_score: number | null;
  superseded_by_version_id: string | null;
  activated_at: string | null;
  retired_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SkillPackVersionWithContent extends SkillPackVersionRead {
  content_md: string;
  files_json: Record<string, string>;
  validation_results: Record<string, unknown>;
}

export interface SkillPackVersionList {
  pack_id: string;
  items: SkillPackVersionRead[];
}

// ─── M4.2 — Skill knowledge graph ────────────────────────────
export type SkillLineageEdgeKind =
  | "derived_from"
  | "supersedes"
  | "forked_from"
  | "pulled_from_hub";

export interface SkillLineageEdgeRead {
  id: string;
  parent_pack_id: string | null;
  child_pack_id: string;
  edge_kind: SkillLineageEdgeKind;
  derived_from_run_ids: string[];
  hub_pack_slug: string | null;
  metadata_json: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface SkillGraphNode {
  node_id: string;
  pack_id: string | null;
  slug: string;
  name: string;
  state: string | null;
  pinned: boolean;
  enabled: boolean;
  is_external: boolean;
  is_focus: boolean;
}

export interface SkillGraphEdge {
  parent_id: string;
  child_id: string;
  kind: SkillLineageEdgeKind;
  derived_from_run_ids: string[];
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface SkillGraphRead {
  focus_pack_id: string;
  depth: number;
  nodes: SkillGraphNode[];
  edges: SkillGraphEdge[];
  truncated: boolean;
}

export interface SkillLineageRead {
  focus_pack_id: string;
  incoming: SkillLineageEdgeRead[];
  outgoing: SkillLineageEdgeRead[];
}

// ─── M4.1 — Runtime console ──────────────────────────────────
export type InflightRunPersistedState =
  | "running"
  | "paused"
  | "completed"
  | "lost"
  | "cancelled"
  | "failed";

export type InflightRunStateBucket =
  | "running"
  | "paused"
  | "lost"
  | "zombie"
  | "killed";

export interface InflightRunRow {
  inflight_run_id: string;
  run_id: string;
  workspace_id: string;
  session_id: string;
  session_label: string | null;
  agent_id: string | null;
  agent_name: string | null;
  identity_id: string | null;
  identity_email: string | null;
  state: InflightRunPersistedState;
  state_bucket: InflightRunStateBucket;
  backend_kind: string;
  started_at: string;
  last_seen_at: string;
  finished_at: string | null;
  elapsed_seconds: number;
  last_event_seq: number;
  token_estimate: number | null;
  error_kind: string | null;
}

export interface InflightRunListOut {
  rows: InflightRunRow[];
  total: number;
}

export interface RuntimeConsoleStats {
  running: number;
  paused: number;
  lost: number;
  zombie: number;
  killed: number;
  total_active: number;
}

export interface ForceRecycleResult {
  run_id: string;
  inflight_run_id: string;
  state: string;
  previous_state: string;
  killed_at: string;
  cancel_dispatched: boolean;
  cancel_error: string | null;
}

// ─── M4.3 — Lineage replay ──────────────────────────────────
export type LineageCompactionStrategy =
  | "sliding_window"
  | "manual"
  | "evolver"
  | "unknown";

export interface LineageNode {
  message_id: string;
  role: string;
  text_excerpt: string;
  created_at: string;
  is_compressed_summary: boolean;
  is_original_turn: boolean;
}

export interface LineageReplayRead {
  summary_message_id: string;
  session_id: string;
  workspace_id: string;
  original_turn_count: number;
  original_turns: LineageNode[];
  compaction_strategy: LineageCompactionStrategy;
  compressed_at: string;
}

export interface LineageSummaryRead {
  summary_message_id: string;
  role: string;
  turn_count: number;
  compaction_strategy: LineageCompactionStrategy;
  compressed_at: string;
  summary_excerpt: string;
}

// ─── Workspace switcher runtime summaries ───────────────────
export interface WorkspaceRuntimeSummary {
  workspace_id: string;
  running: number;
  stuck: number;
  orphan: number;
  queued: number;
}

export interface WorkspaceRuntimeSummariesResponse {
  summaries: WorkspaceRuntimeSummary[];
  timestamp: number;
}

// ─── M4.4 — Project Kanban ──────────────────────────────────
export type BoardCardColumnValue =
  | "backlog"
  | "in_progress"
  | "review"
  | "done";

export type BoardCardPriorityValue = "low" | "normal" | "high" | "urgent";

export const BOARD_COLUMN_ORDER: BoardCardColumnValue[] = [
  "backlog",
  "in_progress",
  "review",
  "done",
];

export const BOARD_PRIORITY_ORDER: BoardCardPriorityValue[] = [
  "low",
  "normal",
  "high",
  "urgent",
];

export interface ProjectBoardRead {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  squad_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface BoardCardRead {
  id: string;
  workspace_id: string;
  board_id: string;
  title: string;
  description: string | null;
  column: BoardCardColumnValue;
  priority: BoardCardPriorityValue;
  assignee_agent_id: string | null;
  assignee_identity_id: string | null;
  sort_order: number;
  due_at: string | null;
  completed_at: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface BoardKanbanRead {
  board: ProjectBoardRead;
  columns: Record<BoardCardColumnValue, BoardCardRead[]>;
}
