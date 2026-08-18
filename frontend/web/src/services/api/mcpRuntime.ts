import { API_BASE } from "./config";
import {
  ApiRequestError,
  apiRequestErrorFromResponse,
  cookieSessionFetch,
} from "./fetch";
import {
  clearMcpGatewayJwt,
  getMcpGatewayJwt,
} from "../../utils/mcpGatewayAuth";

export interface McpRuntimeContextResponse {
  mcp_context_id: string;
  expires_at: string;
}

function projectRuntimeContext(value: unknown): McpRuntimeContextResponse {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("invalid_mcp_runtime_context_response");
  }
  const record = value as Record<string, unknown>;
  if (
    typeof record.mcp_context_id !== "string" ||
    !record.mcp_context_id ||
    typeof record.expires_at !== "string" ||
    !record.expires_at
  ) {
    throw new Error("invalid_mcp_runtime_context_response");
  }
  return {
    mcp_context_id: record.mcp_context_id,
    expires_at: record.expires_at,
  };
}

function requiredMcpJwt(): string {
  const jwt = getMcpGatewayJwt();
  if (jwt) return jwt;
  throw new ApiRequestError(
    "MCP credential is unavailable",
    401,
    "mcp_jwt_missing",
    "rejected_before_persist",
  );
}

export async function createMcpRuntimeContext(): Promise<McpRuntimeContextResponse> {
  const jwt = requiredMcpJwt();

  const response = await cookieSessionFetch(`${API_BASE}/api/ai/mcp/runtime-contexts`, {
    method: "POST",
    cache: "no-store",
    redirect: "error",
    headers: {
      "JWT-Authorization": `Bearer ${jwt}`,
    },
  });
  if (response.status === 401) clearMcpGatewayJwt();
  if (!response.ok) throw await apiRequestErrorFromResponse(response);
  return projectRuntimeContext(await response.json().catch(() => null));
}

export async function prepareMcpRuntimeContext(options: {
  selectedMcpToolIds?: string[];
  profileSelected?: boolean;
}): Promise<string | undefined> {
  const explicitlyRequired =
    (options.selectedMcpToolIds?.length ?? 0) > 0 || options.profileSelected === true;
  if (!explicitlyRequired) return undefined;
  return (await createMcpRuntimeContext()).mcp_context_id;
}
