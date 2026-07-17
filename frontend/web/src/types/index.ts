// ============================================
// Feedback Types (re-export from feedback.ts)
// ============================================
export type {
  RatingValue,
  Feedback,
  FeedbackCreate,
  FeedbackStats,
  FeedbackListResponse,
} from "./feedback";

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
  MarketplaceSkillResponse,
  MarketplaceListResponse,
  MarketplaceCreateRequest,
  MarketplaceSkillFilesResponse,
  MarketplaceSkillFileResponse,
  MarketplaceInstallResponse,
  MarketplaceUpdateResponse,
  TagsResponse,
  PublishToMarketplaceRequest,
} from "./skill";

export type { AgentOption } from "./agentOptions";

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
  type UserCreate,
  type UserUpdate,
  type UserListResponse,
  type RegisterResponse,
  type Role,
  type RoleCreate,
  type RoleListResponse,
  type RoleUpdate,
  type RoleLimits,
  type LoginRequest,
  type TokenResponse,
  type TokenPayload,
  type AuthState,
  type PermissionInfo,
  type PermissionGroup,
  type PermissionsResponse,
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
  MCPImportRequest,
  MCPImportResponse,
  MCPExportResponse,
  MCPServerMoveRequest,
  MCPServerMoveResponse,
  MCPToolInfo,
  MCPToolParamInfo,
  MCPToolDiscoveryResponse,
  MCPToolToggleResponse,
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
// Settings Types
// ============================================
export type {
  SettingType,
  SettingCategory,
  SettingDependsOn,
  SettingItem,
  SettingsResponse,
  SettingUpdate,
  SettingResetResponse,
} from "./settings";

// ============================================
// File Upload Types
// ============================================
export type {
  FileCategory,
  MessageAttachment,
  UploadState,
  UploadConfig,
  UploadResult,
  FileCheckResult,
} from "./upload";

// ============================================
// Share Types
// ============================================
export type {
  ShareType,
  ShareVisibility,
  SharedSession,
  ShareCreate,
  ShareResponse,
  ShareListResponse,
  SharedContentOwner,
  SharedContentResponse,
} from "./share";

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
// Version Types
// ============================================
export type { VersionInfo } from "./common";

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
