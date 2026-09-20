// ============================================
// Message Types
// ============================================
export type {
  Message,
  MessagePart,
  SandboxPart,
  TokenUsagePart,
  RunStatusPart,
  ArtifactPart,
  ToolPermissionPart,
  ToolPermissionDecision,
  ToolPermissionStatus,
  TextPart,
  ThinkingPart,
  ToolPart,
  SubagentPart,
  TodoPart,
  TodoItem,
  TodoStatus,
  ToolCall,
  ToolResult,
  AIMessage,
  RawToolCall,
  ToolMessage,
  DeepAgentState,
  StreamEventData,
  FormFieldType,
  FormField,
  PendingApproval,
  StreamEvent,
  AgentResponse,
  AgentStep,
  ConnectionStatus,
  ConnectionState,
  RunSummary,
  SummaryPart,
} from "./message";

// ============================================
// Skills Types
// ============================================
export type {
  SkillSource,
  UserSkill,
  UserSkillDetail,
  SkillFileResponse,
  SkillToggleResponse,
  SkillResponse,
  PublicSkillResponse,
  SelectedSkillRequest,
  SkillsResponse,
  SkillCreate,
} from "./skill";

export type { AgentOption, AgentThinkingEffort } from "./agentOptions";
export type {
  AgentProfileAdminProjection,
  AgentProfileDraftRequest,
  AgentProfileMutationResponse,
  AgentProfilePublicProjection,
  AgentProfileSkillReference,
  SelectedAgentProfileRequest,
} from "./agentProfile";

// ============================================
// Session Types
// ============================================
export type {
  Session,
  SessionMessage,
  SessionSummary,
  SessionWithMessages,
  SessionListResponse,
  SSEEventRecord,
  SessionEventsResponse,
} from "./session";

// ============================================
// Authentication & Authorization Types
// ============================================
export {
  Permission,
  type User,
  type UserUpdate,
  type UserListResponse,
  type Role,
  type RoleListResponse,
  type RoleLimits,
  type LoginRequest,
  type TokenPayload,
  type AuthState,
} from "./auth";

// ============================================
// MCP Types
// ============================================
export type {
  MCPTransport,
  MCPServerBase,
  MCPServerResponse,
  MCPServersResponse,
  MCPServerCreate,
  MCPServerUpdate,
  MCPServerToggleResponse,
  MCPRoleQuota,
  MCPToolInfo,
  MCPToolParamInfo,
  MCPToolDiscoveryResponse,
} from "./mcp";

// ============================================
// Tool Types
// ============================================
export type {
  ToolCategory,
  ToolParamInfo,
  ToolInfo,
  ToolsListResponse,
  ToolState,
} from "./tool";

// ============================================
// File Upload Types
// ============================================
export type {
  FileCategory,
  MessageAttachment,
  LegacyUploadLimits,
  UploadConfig,
  UploadLimitsBytes,
  UploadResult,
} from "./upload";

// ============================================
// Role Governance Types
// ============================================
export type {
  RoleGovernanceAuditItem,
  RoleGovernanceDecisionRequest,
  RoleGovernanceDepartment,
  RoleGovernanceOperationResponse,
  RoleGovernanceOverviewResponse,
  RoleGovernanceRequestCreate,
  RoleGovernanceRequestItem,
  RoleGovernanceRole,
  RoleGovernanceRoleDirectory,
  RoleGovernanceRollbackRequest,
  RoleGovernanceScope,
  RoleGovernanceSkillAvailability,
  RoleGovernanceWorkbenchGovernance,
  RoleGovernanceWorkspace,
} from "./roleGovernance";

// ============================================
// Project Types
// ============================================

export interface Project {
  id: string;
  user_id: string;
  name: string;
  type: "favorites" | "custom" | "channel";
  icon?: string;
  sort_order: number;
  created_at: string;
  updated_at: string;
}
export * from "./knowledge";
