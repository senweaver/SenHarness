"""Model registry. Importing this module ensures Alembic autogenerate picks every table up."""

from __future__ import annotations

from app.db.models.agent import Agent, AgentVisibility, AutonomyLevel, BackendKind
from app.db.models.agent_profile import AgentProfile
from app.db.models.agent_report import AgentReport, ReportReason, ReportStatus
from app.db.models.agent_star import AgentStar
from app.db.models.agent_version import AgentVersion
from app.db.models.api_key import ApiKey
from app.db.models.approval import Approval, ApprovalResourceType, ApprovalStatus
from app.db.models.attachment import Attachment, AttachmentKind
from app.db.models.audit import AuditEvent
from app.db.models.board_card import (
    BOARD_CARD_COLUMN_VALUES,
    BOARD_CARD_PRIORITY_VALUES,
    BoardCard,
    BoardCardColumn,
    BoardCardPriority,
)
from app.db.models.auth_session import AuthSession
from app.db.models.backend_adapter import (
    BackendAdapter,
    BackendAdapterHealth,
    BackendAdapterKind,
)
from app.db.models.batch import (
    BatchCaseStatus,
    BatchRun,
    BatchRunCase,
    BatchRunStatus,
)
from app.db.models.channel import Channel, ChannelKind
from app.db.models.checkpoint import SessionCheckpoint
from app.db.models.department import Department
from app.db.models.email_verification import EmailVerificationToken
from app.db.models.flow import (
    Flow,
    FlowExecutionMode,
    FlowRun,
    FlowRunOutcome,
    FlowRunStatus,
    FlowTriggerKind,
)
from app.db.models.gateway_message import (
    GatewayMessage,
    GatewayMessageDirection,
    GatewayMessageStatus,
)
from app.db.models.governance import (
    Budget,
    BudgetPeriod,
    GovernanceScope,
    Policy,
    ToolCallLog,
    UsageEvent,
)
from app.db.models.hub_skill_pack import (
    HUB_SCOPE_VALUES,
    HUB_SKILL_PACK_STATE_VALUES,
    HubScope,
    HubSkillPack,
    HubSkillPackState,
)
from app.db.models.hub_skill_pack_version import HubSkillPackVersion
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.inflight_run import (
    ERROR_KIND_MAX_CHARS as INFLIGHT_RUN_ERROR_KIND_MAX_CHARS,
)
from app.db.models.inflight_run import (
    PID_TOKEN_MAX_CHARS as INFLIGHT_RUN_PID_TOKEN_MAX_CHARS,
)
from app.db.models.inflight_run import InflightRun, InflightRunState
from app.db.models.invitation import Invitation
from app.db.models.job_run import (
    ARGS_JSON_MAX_BYTES as JOB_RUN_ARGS_JSON_MAX_BYTES,
)
from app.db.models.job_run import (
    ERROR_MESSAGE_MAX_CHARS as JOB_RUN_ERROR_MESSAGE_MAX_CHARS,
)
from app.db.models.job_run import JobRun, JobRunStatus
from app.db.models.judge_verdict import JudgeVerdict
from app.db.models.kb_source import (
    KbAccess,
    KbAccessLevel,
    KbAccessSubjectKind,
    KbSource,
    KbSourceKind,
    KbSourceStatus,
    KbSourceSync,
    KbSyncStatus,
)
from app.db.models.knowledge import (
    KNOWLEDGE_VECTOR_DIM,
    DocSourceKind,
    DocStatus,
    KnowledgeChunk,
    KnowledgeCollection,
    KnowledgeDoc,
)
from app.db.models.logical_thread import LogicalThread, ThreadChannelBinding
from app.db.models.mcp import McpServer, McpServerHealth, McpTransport, ToolBinding, Toolbox
from app.db.models.membership import Membership, MembershipStatus
from app.db.models.memory import MEMORY_VECTOR_DIM, Memory, MemoryKind, MemoryScope
from app.db.models.memory_profile import (
    MAX_CONTENT_CHARS as MEMORY_PROFILE_MAX_CONTENT_CHARS,
)
from app.db.models.memory_profile import (
    SOUL_DIMENSIONS,
    MemoryProfile,
    MemoryProfileKind,
)
from app.db.models.message import (
    COMPACTION_STRATEGIES,
    LINEAGE_TEXT_EXCERPT_MAX_CHARS,
    Message,
    MessageRole,
)
from app.db.models.message_rating import MessageRating
from app.db.models.model_provider import (
    CredentialType,
    ModelKey,
    ModelProvider,
    ModelRoute,
    ProviderKind,
    ProviderModel,
)
from app.db.models.notification import Notification, NotificationLevel
from app.db.models.pending_memory import (
    PendingMemory,
    PendingMemoryStatus,
    PendingMemoryTargetTable,
)
from app.db.models.plugin_registry import PluginRegistry, PluginRegistryStatus
from app.db.models.project_board import ProjectBoard
from app.db.models.retention_watermark import RetentionScopeKind, RetentionWatermark
from app.db.models.role import BuiltinRole, Role
from app.db.models.search_provider import SearchProvider, SearchProviderKind
from app.db.models.session import Session, SessionKind, SessionState
from app.db.models.session_artifact import SessionArtifact
from app.db.models.session_goal import GoalAlignmentScore, SessionGoal
from app.db.models.session_share import SessionShare, SharePermission, ShareVisibility
from app.db.models.session_star import SessionStar
from app.db.models.skill_lineage_edge import (
    SKILL_LINEAGE_EDGE_KIND_VALUES,
    SkillLineageEdge,
    SkillLineageEdgeKind,
)
from app.db.models.skill_pack_version import (
    SKILL_PACK_VERSION_STATE_VALUES,
    SkillPackVersion,
    SkillPackVersionState,
)
from app.db.models.skill_usage import SkillUsage, SkillUsageEventKind
from app.db.models.skills import (
    AgentSkill,
    SkillFile,
    SkillPack,
    SkillPackSource,
    SkillPackState,
)
from app.db.models.squad import Squad, SquadMember, SquadStrategy
from app.db.models.squad_star import SquadStar
from app.db.models.subagent_run import (
    FINAL_OUTPUT_MAX_CHARS as SUBAGENT_FINAL_OUTPUT_MAX_CHARS,
)
from app.db.models.subagent_run import SubAgentRun, SubAgentRunState
from app.db.models.system_settings import SystemSetting
from app.db.models.token_blacklist import TokenBlacklist
from app.db.models.tombstone_slug import TombstoneSlug
from app.db.models.user_profile import (
    AUTO_INJECT_CONFIDENCE_THRESHOLD as USER_PROFILE_AUTO_INJECT_CONFIDENCE_THRESHOLD,
)
from app.db.models.user_profile import (
    MAX_FACT_CHARS as USER_PROFILE_MAX_FACT_CHARS,
)
from app.db.models.user_profile import (
    USER_PROFILE_DIMENSIONS,
    UserProfileDimension,
    UserProfileFact,
)
from app.db.models.vault import KekKey, VaultItem, VaultItemKind
from app.db.models.workspace import DEFAULT_BRANDING, Workspace, WorkspacePlan, WorkspaceType
from app.db.models.workspace_creation_log import CreationKind, WorkspaceCreationLog
from app.db.models.workspace_hub_subscription import WorkspaceHubSubscription

__all__ = [
    "BOARD_CARD_COLUMN_VALUES",
    "BOARD_CARD_PRIORITY_VALUES",
    "COMPACTION_STRATEGIES",
    "DEFAULT_BRANDING",
    "HUB_SCOPE_VALUES",
    "HUB_SKILL_PACK_STATE_VALUES",
    "INFLIGHT_RUN_ERROR_KIND_MAX_CHARS",
    "INFLIGHT_RUN_PID_TOKEN_MAX_CHARS",
    "JOB_RUN_ARGS_JSON_MAX_BYTES",
    "JOB_RUN_ERROR_MESSAGE_MAX_CHARS",
    "KNOWLEDGE_VECTOR_DIM",
    "LINEAGE_TEXT_EXCERPT_MAX_CHARS",
    "MEMORY_PROFILE_MAX_CONTENT_CHARS",
    "MEMORY_VECTOR_DIM",
    "SKILL_LINEAGE_EDGE_KIND_VALUES",
    "SKILL_PACK_VERSION_STATE_VALUES",
    "SOUL_DIMENSIONS",
    "Agent",
    "AgentProfile",
    "AgentReport",
    "AgentSkill",
    "AgentStar",
    "AgentVersion",
    "AgentVisibility",
    "ApiKey",
    "Approval",
    "ApprovalResourceType",
    "ApprovalStatus",
    "Attachment",
    "AttachmentKind",
    "AuditEvent",
    "AuthSession",
    "AutonomyLevel",
    "BackendAdapter",
    "BackendAdapterHealth",
    "BackendAdapterKind",
    "BackendKind",
    "BatchCaseStatus",
    "BatchRun",
    "BatchRunCase",
    "BatchRunStatus",
    "BoardCard",
    "BoardCardColumn",
    "BoardCardPriority",
    "Budget",
    "BudgetPeriod",
    "BuiltinRole",
    "Channel",
    "ChannelKind",
    "CreationKind",
    "CredentialType",
    "Department",
    "DocSourceKind",
    "DocStatus",
    "EmailVerificationToken",
    "Flow",
    "FlowExecutionMode",
    "FlowRun",
    "FlowRunOutcome",
    "FlowRunStatus",
    "FlowTriggerKind",
    "GatewayMessage",
    "GatewayMessageDirection",
    "GatewayMessageStatus",
    "GoalAlignmentScore",
    "GovernanceScope",
    "HubScope",
    "HubSkillPack",
    "HubSkillPackState",
    "HubSkillPackVersion",
    "Identity",
    "IdentityStatus",
    "InflightRun",
    "InflightRunState",
    "Invitation",
    "JobRun",
    "JobRunStatus",
    "JudgeVerdict",
    "KbAccess",
    "KbAccessLevel",
    "KbAccessSubjectKind",
    "KbSource",
    "KbSourceKind",
    "KbSourceStatus",
    "KbSourceSync",
    "KbSyncStatus",
    "KekKey",
    "KnowledgeChunk",
    "KnowledgeCollection",
    "KnowledgeDoc",
    "LogicalThread",
    "McpServer",
    "McpServerHealth",
    "McpTransport",
    "Membership",
    "MembershipStatus",
    "Memory",
    "MemoryKind",
    "MemoryProfile",
    "MemoryProfileKind",
    "MemoryScope",
    "Message",
    "MessageRating",
    "MessageRole",
    "ModelKey",
    "ModelProvider",
    "ModelRoute",
    "Notification",
    "NotificationLevel",
    "PendingMemory",
    "PendingMemoryStatus",
    "PendingMemoryTargetTable",
    "PlatformRole",
    "PluginRegistry",
    "PluginRegistryStatus",
    "Policy",
    "ProjectBoard",
    "ProviderKind",
    "ProviderModel",
    "ReportReason",
    "ReportStatus",
    "RetentionScopeKind",
    "RetentionWatermark",
    "Role",
    "SearchProvider",
    "SearchProviderKind",
    "Session",
    "SessionArtifact",
    "SessionCheckpoint",
    "SessionGoal",
    "SessionKind",
    "SessionShare",
    "SessionStar",
    "SessionState",
    "SharePermission",
    "ShareVisibility",
    "SkillFile",
    "SkillLineageEdge",
    "SkillLineageEdgeKind",
    "SkillPack",
    "SkillPackSource",
    "SkillPackState",
    "SkillPackVersion",
    "SkillPackVersionState",
    "SkillUsage",
    "SkillUsageEventKind",
    "SUBAGENT_FINAL_OUTPUT_MAX_CHARS",
    "Squad",
    "SquadMember",
    "SquadStar",
    "SquadStrategy",
    "SubAgentRun",
    "SubAgentRunState",
    "SystemSetting",
    "ThreadChannelBinding",
    "TokenBlacklist",
    "TombstoneSlug",
    "ToolBinding",
    "ToolCallLog",
    "Toolbox",
    "USER_PROFILE_AUTO_INJECT_CONFIDENCE_THRESHOLD",
    "USER_PROFILE_DIMENSIONS",
    "USER_PROFILE_MAX_FACT_CHARS",
    "UsageEvent",
    "UserProfileDimension",
    "UserProfileFact",
    "VaultItem",
    "VaultItemKind",
    "Workspace",
    "WorkspaceCreationLog",
    "WorkspaceHubSubscription",
    "WorkspacePlan",
    "WorkspaceType",
]
