"""Model registry. Importing this module ensures Alembic autogenerate picks every table up."""

from __future__ import annotations

from app.db.models.agent import Agent, AgentVisibility, AutonomyLevel, BackendKind
from app.db.models.agent_report import AgentReport, ReportReason, ReportStatus
from app.db.models.agent_star import AgentStar
from app.db.models.agent_version import AgentVersion
from app.db.models.api_key import ApiKey
from app.db.models.approval import Approval, ApprovalStatus
from app.db.models.attachment import Attachment, AttachmentKind
from app.db.models.audit import AuditEvent
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
from app.db.models.flow import Flow, FlowRun, FlowRunStatus, FlowTriggerKind
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
from app.db.models.identity import Identity, IdentityStatus, PlatformRole
from app.db.models.invitation import Invitation
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
from app.db.models.mcp import McpServer, McpServerHealth, ToolBinding, Toolbox
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
from app.db.models.message import Message, MessageRole
from app.db.models.message_rating import MessageRating
from app.db.models.model_provider import ModelKey, ModelProvider, ModelRoute, ProviderKind
from app.db.models.notification import Notification, NotificationLevel
from app.db.models.role import BuiltinRole, Role
from app.db.models.session import Session, SessionKind, SessionState
from app.db.models.session_share import SessionShare, SharePermission, ShareVisibility
from app.db.models.skills import AgentSkill, SkillFile, SkillPack, SkillPackSource
from app.db.models.squad import Squad, SquadMember, SquadStrategy
from app.db.models.token_blacklist import TokenBlacklist
from app.db.models.vault import KekKey, VaultItem, VaultItemKind
from app.db.models.workspace import DEFAULT_BRANDING, Workspace, WorkspacePlan, WorkspaceType

__all__ = [
    "DEFAULT_BRANDING",
    "KNOWLEDGE_VECTOR_DIM",
    "MEMORY_PROFILE_MAX_CONTENT_CHARS",
    "MEMORY_VECTOR_DIM",
    "SOUL_DIMENSIONS",
    "Agent",
    "AgentReport",
    "AgentSkill",
    "AgentStar",
    "AgentVersion",
    "AgentVisibility",
    "ApiKey",
    "Approval",
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
    "Budget",
    "BudgetPeriod",
    "BuiltinRole",
    "Channel",
    "ChannelKind",
    "Department",
    "DocSourceKind",
    "DocStatus",
    "Flow",
    "FlowRun",
    "FlowRunStatus",
    "FlowTriggerKind",
    "GatewayMessage",
    "GatewayMessageDirection",
    "GatewayMessageStatus",
    "GovernanceScope",
    "Identity",
    "IdentityStatus",
    "Invitation",
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
    "McpServer",
    "McpServerHealth",
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
    "PlatformRole",
    "Policy",
    "ProviderKind",
    "ReportReason",
    "ReportStatus",
    "Role",
    "Session",
    "SessionCheckpoint",
    "SessionKind",
    "SessionShare",
    "SessionState",
    "SharePermission",
    "ShareVisibility",
    "SkillFile",
    "SkillPack",
    "SkillPackSource",
    "Squad",
    "SquadMember",
    "SquadStrategy",
    "TokenBlacklist",
    "ToolBinding",
    "ToolCallLog",
    "Toolbox",
    "UsageEvent",
    "VaultItem",
    "VaultItemKind",
    "Workspace",
    "WorkspacePlan",
    "WorkspaceType",
]
