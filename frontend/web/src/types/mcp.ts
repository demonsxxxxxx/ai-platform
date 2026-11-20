// ============================================
// MCP Types
// ============================================

// MCP Transport Type
export type MCPTransport = "sse" | "streamable_http" | "sandbox";
export type MCPIntegrationKind = "mcp" | "enterprise_gateway";

export type MCPPublicOperationSummary = { action_label: string; object_label: string; object_path: string[] };

export interface MCPPluginConfiguration {
  display_name: string;
  description: string;
  user_authorization: "none" | "profile_drive";
  default_tools?: string[];
}

/** Public user projection intentionally excludes connection and distribution material. */
export interface MCPPluginResponse {
  name: string;
  display_name: string;
  description: string;
  installation: "system_preinstalled";
  integration_kind?: MCPIntegrationKind;
  user_authorization: "none" | "profile_drive";
  status: "active" | "disabled";
  enabled: boolean;
  can_edit: false;
}

export interface MCPConnectionTestResponse {
  status: "ready" | "authorization_required" | "discovery_failed" | "not_configured";
  checked_at: string;
  tools?: { name: string; description: string }[];
  count?: number;
}

// MCP Server Base
export interface MCPServerBase {
  name: string;
  transport: MCPTransport;
  integration_kind?: MCPIntegrationKind;
  enabled: boolean;
  url?: string;
  headers?: Record<string, string>;
  public_operation_summaries?: Record<string, MCPPublicOperationSummary>;
  plugin?: MCPPluginConfiguration;
  credentials_action?: "keep" | "replace" | "clear";
}

// MCP Server Response (from API)
export interface MCPServerResponse extends MCPServerBase {
  is_system: boolean;
  can_edit: boolean;
  allowed_roles: string[];
  allowed_departments?: string[];
  role_quotas: Record<string, MCPRoleQuota>;
  credential_state?: "not_configured" | "configured" | "platform_managed";
  credential_metadata?: Record<string, unknown>;
  contract_version?: string;
  created_at?: string;
  updated_at?: string;
}

export interface MCPRoleQuota {
  daily_limit?: number | null;
  weekly_limit?: number | null;
}

// MCP Servers List Response
export interface MCPServersResponse {
  servers: MCPServerResponse[];
  total: number;
  skip: number;
  limit: number;
}

// MCP Server Create Request
export interface MCPServerCreate {
  name: string;
  transport: Exclude<MCPTransport, "sandbox">;
  integration_kind?: MCPIntegrationKind;
  enabled?: boolean;
  url?: string;
  headers?: Record<string, string>;
  public_operation_summaries?: Record<string, MCPPublicOperationSummary>;
  allowed_roles?: string[];
  role_quotas?: Record<string, MCPRoleQuota>;
  department_ids?: string[];
  plugin?: MCPPluginConfiguration;
  credentials_action?: "keep" | "replace" | "clear";
}

// MCP Server Update Request
export interface MCPServerUpdate {
  transport?: Exclude<MCPTransport, "sandbox">;
  integration_kind?: MCPIntegrationKind;
  enabled?: boolean;
  url?: string;
  headers?: Record<string, string>;
  public_operation_summaries?: Record<string, MCPPublicOperationSummary>;
  allowed_roles?: string[];
  role_quotas?: Record<string, MCPRoleQuota>;
  department_ids?: string[];
  plugin?: MCPPluginConfiguration;
  credentials_action?: "keep" | "replace" | "clear";
}

// MCP Toggle Response
export interface MCPServerToggleResponse {
  server: MCPServerResponse;
  message: string;
}

// MCP Tool Info (discovered from server)
export interface MCPToolInfo {
  available?: boolean;
  /** Optional safe upstream display name; it never identifies or routes a call. */
  upstream_server?: string | null;
  name: string;
  description: string;
  parameters: MCPToolParamInfo[];
  system_disabled?: boolean; // Whether this tool is disabled at system level
  user_disabled?: boolean; // Whether this tool is disabled by the user
}

// MCP Tool Parameter Info
export interface MCPToolParamInfo {
  name: string;
  type: string;
  description: string;
  required: boolean;
  default?: unknown;
}

// MCP Tool Discovery Response
export interface MCPToolDiscoveryResponse {
  server_name: string;
  tools: MCPToolInfo[];
  count: number;
  unavailable_reason: string | null;
  error?: string;
}
