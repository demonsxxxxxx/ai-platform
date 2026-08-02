/**
 * API service for backend communication
 * 支持JWT认证的API服务
 *
 * 此文件作为统一导出入口，所有 API 模块拆分在 ./api/ 目录下
 */

// Config
export { API_BASE, getFullUrl } from "./api/config";

// Token management
export {
  getAccessToken,
  getRefreshToken,
  setTokens,
  clearTokens,
  isAuthenticated,
  decodeToken,
  isTokenExpired,
  getRedirectPath,
  clearRedirectPath,
} from "./api/token";

// Auth fetch
export { authFetch } from "./api/fetch";

// API modules
export { authApi, buildOAuthLoginUrl } from "./api/auth";
export { roleApi } from "./api/role";
export {
  DEFAULT_CHAT_AGENT_ID,
  isChatStreamNeedsConfirmation,
  resolveChatSessionAgentId,
  sessionApi,
  type BackendSession,
  type ChatIntentDecision,
  type ChatStreamResponse,
  type ChatStreamNeedsConfirmationResponse,
  type ChatStreamQueuedResponse,
  type CapabilitySuggestion,
  type SessionListResponse,
  type SessionInputFile,
  type SessionInputFilesResponse,
} from "./api/session";
export { skillApi } from "./api/skill";
export { workbenchApi } from "./api/workbench";
export { mcpApi } from "./api/mcp";
export { envvarApi } from "./api/envvar";
export { uploadApi } from "./api/upload";
export { versionApi } from "./api/version";
export { projectApi } from "./api/project";
export {
  type ToolPermissionHistoryResponse,
  type ToolPermissionHistoryView,
} from "./api/toolPermission";
