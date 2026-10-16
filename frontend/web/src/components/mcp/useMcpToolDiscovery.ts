import { useEffect, useMemo, useRef, useState } from "react";
import { mcpApi } from "../../services/api/mcp";
import type { MCPServerResponse, MCPToolDiscoveryResponse, MCPToolInfo } from "../../types";

export const MCP_TOOL_DISCOVERY_CONCURRENCY = 6;
export type McpToolDiscoveryStatus = "idle" | "loading" | "ready" | "unavailable" | "disabled" | "not_configured" | "unsupported_transport";

interface McpToolDiscoveryResult {
  toolsByServer: Map<string, MCPToolInfo[]>;
  unavailableServers: Set<string>;
}

export async function collectMcpTools(
  serverNames: string[],
  discover: (name: string) => Promise<MCPToolDiscoveryResponse>,
  shouldContinue: () => boolean = () => true,
): Promise<McpToolDiscoveryResult> {
  const names = Array.from(new Set(serverNames)).filter(Boolean);
  const toolsByServer = new Map<string, MCPToolInfo[]>();
  const unavailableServers = new Set<string>();
  let nextIndex = 0;
  await Promise.all(Array.from({ length: Math.min(MCP_TOOL_DISCOVERY_CONCURRENCY, names.length) }, async () => {
    while (shouldContinue() && nextIndex < names.length) {
      const name = names[nextIndex++];
      try {
        const response = await discover(name);
        if (response.unavailable_reason !== null || response.server_name !== name ||
          !Array.isArray(response.tools) || !Number.isInteger(response.count) || response.count !== response.tools.filter((tool) => tool?.available !== false).length ||
          response.tools.some((tool) => !tool || typeof tool.name !== "string" || !tool.name || typeof tool.description !== "string" || (tool.available !== undefined && typeof tool.available !== "boolean")) ||
          new Set(response.tools.map((tool) => tool.name)).size !== response.tools.length) {
          unavailableServers.add(name);
        } else {
          toolsByServer.set(name, response.tools.map((tool) => ({ name: tool.name, description: tool.description, parameters: [], ...(tool.available !== undefined ? { available: tool.available } : {}) })));
        }
      } catch {
        unavailableServers.add(name);
      }
    }
  }));
  return { toolsByServer, unavailableServers };
}

export function useMcpToolDiscovery(servers: MCPServerResponse[], scope: string, enabled: boolean, discover = mcpApi.discoverTools) {
  const [revision, setRevision] = useState(0);
  const [result, setResult] = useState<McpToolDiscoveryResult & { key: string }>(() => ({ key: "", toolsByServer: new Map(), unavailableServers: new Set() }));
  const serverStates = useMemo(() => new Map(servers.map((server): [string, McpToolDiscoveryStatus] => [server.name,
    !server.enabled ? "disabled" : server.transport === "sandbox" ? "unsupported_transport" : server.credential_state === "not_configured" ? "not_configured" : "idle",
  ])), [servers]);
  const key = JSON.stringify([scope, enabled, Array.from(serverStates), revision]);
  const activeKeyRef = useRef(key);
  activeKeyRef.current = key;

  useEffect(() => {
    let active = true;
    const isCurrent = () => active && activeKeyRef.current === key;
    if (enabled) {
      const names = Array.from(serverStates).filter(([, status]) => status === "idle").map(([name]) => name);
      void collectMcpTools(names, discover, isCurrent).then((discovered) => {
        if (isCurrent()) setResult({ key, ...discovered });
      });
    }
    return () => { active = false; };
  }, [enabled, key, serverStates, discover]);

  const current = enabled && result.key === key;
  const statusByServer = new Map(serverStates);
  for (const [name, status] of statusByServer) {
    if (status === "idle" && enabled) statusByServer.set(name, !current ? "loading" : result.toolsByServer.has(name) ? "ready" : "unavailable");
  }
  return {
    statusByServer,
    toolsByServer: current ? result.toolsByServer : new Map<string, MCPToolInfo[]>(),
    refresh: () => {
      if (enabled && activeKeyRef.current === key) setRevision((value) => value + 1);
    },
  };
}
