import { useState, useCallback, useEffect } from "react";
import { authenticatedRequest } from "../services/api/authenticatedRequest";
import type { ToolState } from "../types";

interface ChatMcpCatalogItem {
  tool_id: string;
  label: string;
  description: string;
  category: "mcp";
}

interface ChatMcpCatalogResponse {
  tools?: ChatMcpCatalogItem[];
  selected_mcp_tool_ids?: string[];
}

type ChatMcpToolState = ToolState & { label?: string };

export function useTools(options?: { enabled?: boolean; sessionId?: string | null }) {
  const hookEnabled = options?.enabled !== false;
  const sessionId = options?.sessionId ?? null;
  const [tools, setTools] = useState<ChatMcpToolState[]>([]);
  const [serverSelectedToolIds, setServerSelectedToolIds] = useState<string[] | undefined>(
    undefined,
  );
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchTools = useCallback(async () => {
    if (!hookEnabled) {
      setTools([]);
      setServerSelectedToolIds(undefined);
      setIsLoading(false);
      setError(null);
      return;
    }
    setIsLoading(true);
    setError(null);
    try {
      const query = sessionId
        ? `?session_id=${encodeURIComponent(sessionId)}`
        : "";
      const rawResponse = await authenticatedRequest(`/api/mcp/chat-tools${query}`);
      if (!rawResponse.ok) throw new Error("Failed to fetch tools");
      const response = (await rawResponse.json()) as ChatMcpCatalogResponse;
      const catalog = Array.isArray(response.tools) ? response.tools : [];
      const canonicalTools = catalog
        .filter(
          (tool) =>
            typeof tool.tool_id === "string" &&
            tool.tool_id.length > 0 &&
            typeof tool.label === "string" &&
            typeof tool.description === "string" &&
            tool.category === "mcp",
        )
        .map((tool) => ({
          name: tool.tool_id,
          label: tool.label,
          description: tool.description,
          category: "mcp" as const,
          server: undefined,
          parameters: [],
          system_disabled: false,
          user_disabled: false,
          enabled: false,
        }));
      setTools(canonicalTools);
      setServerSelectedToolIds(
        sessionId && Array.isArray(response.selected_mcp_tool_ids)
          ? response.selected_mcp_tool_ids
          : undefined,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch tools");
      setTools([]);
      setServerSelectedToolIds(undefined);
    } finally {
      setIsLoading(false);
    }
  }, [hookEnabled, sessionId]);

  useEffect(() => {
    fetchTools();
  }, [fetchTools]);

  useEffect(() => {
    const handleMcpToolsChanged = () => {
      fetchTools();
    };
    window.addEventListener("mcp-tools-changed", handleMcpToolsChanged);
    return () => window.removeEventListener("mcp-tools-changed", handleMcpToolsChanged);
  }, [fetchTools]);

  return {
    tools,
    serverSelectedToolIds,
    isLoading,
    error,
    totalCount: tools.length,
    refreshTools: fetchTools,
  };
}
