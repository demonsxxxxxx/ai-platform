import { useCallback, useEffect, useRef, useState } from "react";
import { mcpApi } from "../services/api/mcp";
import { parseChatMcpCatalogResponse } from "./useTools";
import type { ToolState } from "../types";

interface Snapshot {
  scope: string;
  tools: ToolState[];
  unavailableServers: string[];
  isLoading: boolean;
  error: string | null;
}

/** Admin catalog state never enters the ordinary Chat selection cache. */
export function useMcpAdminCatalog(identityScope: string, enabled = true) {
  const scope = JSON.stringify([identityScope, enabled]);
  const scopeRef = useRef(scope);
  scopeRef.current = scope;
  const generation = useRef(0);
  const [snapshot, setSnapshot] = useState<Snapshot>({
    scope, tools: [], unavailableServers: [], isLoading: enabled, error: null,
  });
  const refreshTools = useCallback(async () => {
    const request = ++generation.current;
    if (!enabled) return;
    setSnapshot({ scope, tools: [], unavailableServers: [], isLoading: true, error: null });
    try {
      const raw = await mcpApi.getCatalogTools();
      const parsed = parseChatMcpCatalogResponse(raw);
      const definitions = (raw as { tools: { available?: boolean }[] }).tools;
      if (definitions.some(tool => tool.available !== undefined && typeof tool.available !== "boolean")) {
        throw new Error("mcp_catalog_availability_invalid");
      }
      const tools = parsed.tools.map((tool, index) => ({ ...tool, system_disabled: definitions[index].available === false }));
      if (request !== generation.current || scopeRef.current !== scope) return;
      const unavailableServers = (raw as { unavailable: { label: string }[] }).unavailable.map(item => item.label);
      setSnapshot({ scope, tools, unavailableServers, isLoading: false,
        error: unavailableServers.length ? "mcp_catalog_unavailable" : null });
    } catch {
      if (request !== generation.current || scopeRef.current !== scope) return;
      setSnapshot({ scope, tools: [], unavailableServers: [], isLoading: false, error: "mcp_catalog_unavailable" });
    }
  }, [scope, enabled]);
  useEffect(() => {
    const requests = generation;
    void refreshTools();
    return () => { requests.current++; };
  }, [refreshTools]);
  const current = enabled && snapshot.scope === scope
    ? snapshot : { scope, tools: [], unavailableServers: [], isLoading: enabled, error: null };
  return { ...current, refreshTools };
}
