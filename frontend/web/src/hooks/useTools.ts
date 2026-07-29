import {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { authenticatedRequest } from "../services/api/authenticatedRequest";
import type { ToolState } from "../types";

export type ChatMcpCatalogStatus = "loading" | "ready" | "empty" | "degraded" | "error";
export type ChatMcpUnavailableMessageKey =
  | "tools.catalog.unavailable.discoveryFailed"
  | "tools.catalog.unavailable.noTools"
  | "tools.catalog.unavailable.generic";

interface ParsedChatMcpCatalog {
  tools: ChatMcpToolState[];
  unavailable: ChatMcpUnavailableMessageKey[];
  selectedMcpToolIds: string[] | undefined;
}

interface ChatMcpCatalogSnapshot extends ParsedChatMcpCatalog {
  generation: number;
  status: ChatMcpCatalogStatus;
}

export interface ChatMcpCatalogState {
  status: ChatMcpCatalogStatus;
  unavailable: ChatMcpUnavailableMessageKey[];
}

export interface ChatMcpCatalogContextValue {
  catalogState: ChatMcpCatalogState;
  retryTools?: () => void;
}

type ChatMcpToolState = ToolState & { label?: string };

const EMPTY_CATALOG: ParsedChatMcpCatalog = {
  tools: [],
  unavailable: [],
  selectedMcpToolIds: undefined,
};

/** Exposes only the bounded catalog state needed by the selector. */
export const ChatMcpCatalogContext = createContext<ChatMcpCatalogContextValue>({
  catalogState: { status: "empty", unavailable: [] },
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function unavailableMessageKey(reason: string): ChatMcpUnavailableMessageKey {
  switch (reason) {
    case "discovery_failed":
      return "tools.catalog.unavailable.discoveryFailed";
    case "no_tools":
      return "tools.catalog.unavailable.noTools";
    default:
      return "tools.catalog.unavailable.generic";
  }
}

function parseChatMcpTool(value: unknown): ChatMcpToolState | null {
  if (!isRecord(value)) return null;
  if (
    !isNonEmptyString(value.tool_id) ||
    !isNonEmptyString(value.label) ||
    typeof value.description !== "string" ||
    value.category !== "mcp"
  ) {
    return null;
  }

  return {
    name: value.tool_id,
    label: value.label,
    description: value.description,
    category: "mcp",
    server: undefined,
    parameters: [],
    system_disabled: false,
    user_disabled: false,
    enabled: false,
  };
}

/** Fail closed and project a catalog response into public-safe tool data. */
export function parseChatMcpCatalogResponse(raw: unknown): ParsedChatMcpCatalog {
  if (!isRecord(raw) || !Array.isArray(raw.tools) || !Array.isArray(raw.unavailable)) {
    throw new Error("chat_mcp_catalog_schema_invalid");
  }
  const count = raw.count;
  if (
    typeof count !== "number" ||
    !Number.isSafeInteger(count) ||
    count < 0 ||
    count !== raw.tools.length
  ) {
    throw new Error("chat_mcp_catalog_count_invalid");
  }

  const tools = raw.tools.map(parseChatMcpTool);
  if (tools.some((tool) => tool === null)) {
    throw new Error("chat_mcp_catalog_tool_invalid");
  }
  const canonicalTools = tools as ChatMcpToolState[];
  if (new Set(canonicalTools.map((tool) => tool.name)).size !== canonicalTools.length) {
    throw new Error("chat_mcp_catalog_tool_duplicate");
  }

  const unavailable = raw.unavailable.map((item) => {
    if (!isRecord(item) || !isNonEmptyString(item.label) || !isNonEmptyString(item.reason)) {
      throw new Error("chat_mcp_catalog_unavailable_invalid");
    }
    return unavailableMessageKey(item.reason);
  });

  if (
    raw.selected_mcp_tool_ids !== undefined &&
    (!Array.isArray(raw.selected_mcp_tool_ids) ||
      raw.selected_mcp_tool_ids.some((toolId) => !isNonEmptyString(toolId)) ||
      new Set(raw.selected_mcp_tool_ids).size !== raw.selected_mcp_tool_ids.length)
  ) {
    throw new Error("chat_mcp_catalog_selection_invalid");
  }

  return {
    tools: canonicalTools,
    unavailable,
    selectedMcpToolIds: raw.selected_mcp_tool_ids as string[] | undefined,
  };
}

/** Classify a successfully validated catalog without conflating empty and degraded states. */
export function classifyChatMcpCatalog(catalog: ParsedChatMcpCatalog): ChatMcpCatalogStatus {
  if (catalog.unavailable.length > 0) return "degraded";
  return catalog.tools.length > 0 ? "ready" : "empty";
}

/** Start a generation-scoped request with no selectable catalog identities. */
export function beginChatMcpCatalogRequest(generation: number): ChatMcpCatalogSnapshot {
  return { generation, status: "loading", ...EMPTY_CATALOG };
}

/** Publish a response only when it still belongs to the active request generation. */
export function publishChatMcpCatalogSuccess(
  current: ChatMcpCatalogSnapshot,
  generation: number,
  catalog: ParsedChatMcpCatalog,
): ChatMcpCatalogSnapshot {
  if (current.generation !== generation) return current;
  return { generation, status: classifyChatMcpCatalog(catalog), ...catalog };
}

/** Publish a retryable failure only when it still belongs to the active request generation. */
export function publishChatMcpCatalogFailure(
  current: ChatMcpCatalogSnapshot,
  generation: number,
): ChatMcpCatalogSnapshot {
  if (current.generation !== generation) return current;
  return { generation, status: "error", ...EMPTY_CATALOG };
}

/** Permit local selection only from a catalog that has selectable tools. */
export function canSelectChatMcpTools(status: ChatMcpCatalogStatus): boolean {
  return status === "ready" || status === "degraded";
}

/** Identify terminal states whose catalog payload passed validation. */
export function hasValidatedChatMcpCatalog(status: ChatMcpCatalogStatus): boolean {
  return status === "ready" || status === "empty" || status === "degraded";
}

/** Retain only IDs authorized by the current validated catalog. */
export function reconcileChatMcpToolSelection(
  selectedToolIds: readonly string[] | undefined,
  tools: readonly ChatMcpToolState[],
  status: ChatMcpCatalogStatus,
): string[] {
  if (!canSelectChatMcpTools(status)) return [];
  const authorizedIds = new Set(tools.map((tool) => tool.name));
  return (selectedToolIds ?? []).filter((toolId) => authorizedIds.has(toolId));
}

export function useTools(options?: { enabled?: boolean; sessionId?: string | null }) {
  const hookEnabled = options?.enabled !== false;
  const sessionId = options?.sessionId ?? null;
  const requestGeneration = useRef(0);
  const [catalog, setCatalog] = useState<ChatMcpCatalogSnapshot>(() =>
    beginChatMcpCatalogRequest(0),
  );

  const fetchTools = useCallback(async () => {
    const generation = ++requestGeneration.current;
    if (!hookEnabled) {
      setCatalog({ generation, status: "empty", ...EMPTY_CATALOG });
      return;
    }

    setCatalog(beginChatMcpCatalogRequest(generation));
    try {
      const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
      const rawResponse = await authenticatedRequest(`/api/mcp/chat-tools${query}`);
      if (!rawResponse.ok) throw new Error("chat_mcp_catalog_request_failed");
      const parsed = parseChatMcpCatalogResponse(await rawResponse.json());
      if (generation !== requestGeneration.current) return;
      setCatalog((current) => publishChatMcpCatalogSuccess(current, generation, {
        ...parsed,
        selectedMcpToolIds: sessionId ? parsed.selectedMcpToolIds : undefined,
      }));
    } catch {
      if (generation !== requestGeneration.current) return;
      setCatalog((current) => publishChatMcpCatalogFailure(current, generation));
    }
  }, [hookEnabled, sessionId]);

  useEffect(() => {
    void fetchTools();
  }, [fetchTools]);

  useEffect(() => {
    const handleMcpToolsChanged = () => {
      void fetchTools();
    };
    window.addEventListener("mcp-tools-changed", handleMcpToolsChanged);
    return () => window.removeEventListener("mcp-tools-changed", handleMcpToolsChanged);
  }, [fetchTools]);

  const catalogState = useMemo<ChatMcpCatalogState>(
    () => ({ status: catalog.status, unavailable: catalog.unavailable }),
    [catalog.status, catalog.unavailable],
  );

  return {
    tools: catalog.tools,
    serverSelectedToolIds: catalog.selectedMcpToolIds,
    isLoading: catalog.status === "loading",
    error: catalog.status === "error" ? "chat_mcp_catalog_unavailable" : null,
    totalCount: catalog.tools.length,
    catalogState,
    refreshTools: fetchTools,
  };
}
