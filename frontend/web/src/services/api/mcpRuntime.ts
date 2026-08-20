import { API_BASE } from "./config";
import {
  apiRequestErrorFromResponse,
  cookieSessionFetch,
} from "./fetch";

export interface McpRuntimeContextResponse {
  mcp_context_id: string;
  expires_at: string;
}

const MCP_RUNTIME_CONTEXT_DISCARD_TIMEOUT_MS = 1_000;

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

export async function createMcpRuntimeContext(
  options: { signal?: AbortSignal } = {},
): Promise<McpRuntimeContextResponse> {
  const response = await cookieSessionFetch(`${API_BASE}/api/ai/mcp/runtime-contexts`, {
    method: "POST",
    cache: "no-store",
    redirect: "error",
    signal: options.signal,
  });
  if (!response.ok) {
    throw await apiRequestErrorFromResponse(response);
  }
  return projectRuntimeContext(await response.json().catch(() => null));
}

export async function discardMcpRuntimeContext(
  contextId: string,
): Promise<void> {
  if (!contextId) return;
  const controller = new AbortController();
  const timeout = globalThis.setTimeout(
    () => controller.abort(),
    MCP_RUNTIME_CONTEXT_DISCARD_TIMEOUT_MS,
  );
  try {
    await cookieSessionFetch(
      `${API_BASE}/api/ai/mcp/runtime-contexts/${encodeURIComponent(contextId)}`,
      {
        method: "DELETE",
        cache: "no-store",
        redirect: "error",
        signal: controller.signal,
      },
    );
  } catch {
    // The server TTL remains the final cleanup fence.
  } finally {
    globalThis.clearTimeout(timeout);
  }
}

export async function prepareMcpRuntimeContext(options: {
  selectedMcpToolIds?: string[];
  profileSelected?: boolean;
  signal?: AbortSignal;
}): Promise<string | undefined> {
  const explicitlyRequired =
    (options.selectedMcpToolIds?.length ?? 0) > 0 || options.profileSelected === true;
  if (!explicitlyRequired) return undefined;
  return (await createMcpRuntimeContext({ signal: options.signal })).mcp_context_id;
}
