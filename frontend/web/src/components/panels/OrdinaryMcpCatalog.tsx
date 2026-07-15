import { useEffect, useMemo, useRef, useState } from "react";
import { Server, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import { mcpApi } from "../../services/api/mcp";
import type { MCPServerResponse, MCPToolInfo } from "../../types";
import { PanelHeader } from "../common/PanelHeader";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { projectOrdinaryMcpCatalogItem } from "./ordinaryCatalogPolicy";

interface OrdinaryMcpCatalogProps {
  servers: MCPServerResponse[];
  isLoading: boolean;
  listError: string | null;
}

export const ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY = 6;

export interface OrdinaryMcpToolDiscoveryResult {
  toolsByServer: Record<string, MCPToolInfo[]>;
  unavailable: boolean;
}

export interface OrdinaryMcpToolDiscoveryState {
  generation: number;
  serverSetKey: string;
  toolsByServer: Record<string, MCPToolInfo[]>;
  toolsLoading: boolean;
  toolsUnavailable: boolean;
}

/** Builds the stable identity used to match discovery state to the current server set. */
// eslint-disable-next-line react-refresh/only-export-components -- shared with pure discovery coverage.
export function buildOrdinaryMcpServerSetKey(serverNames: string[]): string {
  return Array.from(new Set(serverNames.filter(Boolean))).sort().join("\u0000");
}

/** Collects all public tool descriptions with a bounded number of active requests. */
// eslint-disable-next-line react-refresh/only-export-components -- shared with pure discovery coverage.
export async function collectOrdinaryMcpTools(
  serverNames: string[],
  discoverTools: (serverName: string) => Promise<MCPToolInfo[]>,
  shouldContinue: () => boolean = () => true,
): Promise<OrdinaryMcpToolDiscoveryResult> {
  const uniqueServerNames = Array.from(new Set(serverNames)).filter(Boolean);
  const toolsByServer: Record<string, MCPToolInfo[]> = {};
  let unavailable = false;
  let nextIndex = 0;
  const workerCount = Math.min(
    ORDINARY_MCP_TOOL_DISCOVERY_CONCURRENCY,
    uniqueServerNames.length,
  );

  await Promise.all(
    Array.from({ length: workerCount }, async () => {
      while (true) {
        if (!shouldContinue()) return;
        if (nextIndex >= uniqueServerNames.length) return;
        const serverName = uniqueServerNames[nextIndex];
        nextIndex += 1;
        try {
          toolsByServer[serverName] = await discoverTools(serverName);
        } catch {
          unavailable = true;
        }
      }
    }),
  );

  return { toolsByServer, unavailable };
}

/** Starts a new atomic ordinary MCP tool-discovery generation. */
// eslint-disable-next-line react-refresh/only-export-components -- shared with pure discovery coverage.
export function beginOrdinaryMcpToolDiscovery(
  generation: number,
  serverNames: string[],
): OrdinaryMcpToolDiscoveryState {
  return {
    generation,
    serverSetKey: buildOrdinaryMcpServerSetKey(serverNames),
    toolsByServer: {},
    toolsLoading: serverNames.length > 0,
    toolsUnavailable: false,
  };
}

/** Publishes a completed tool-discovery result only for its active generation. */
// eslint-disable-next-line react-refresh/only-export-components -- shared with pure discovery coverage.
export function publishOrdinaryMcpToolDiscovery(
  state: OrdinaryMcpToolDiscoveryState,
  generation: number,
  result: OrdinaryMcpToolDiscoveryResult,
): OrdinaryMcpToolDiscoveryState {
  if (state.generation !== generation) return state;
  return {
    generation,
    serverSetKey: state.serverSetKey,
    toolsByServer: result.toolsByServer,
    toolsLoading: false,
    toolsUnavailable: result.unavailable,
  };
}

/** Hides discovery output that belongs to a different current server set. */
// eslint-disable-next-line react-refresh/only-export-components -- shared with pure discovery coverage.
export function resolveVisibleOrdinaryMcpToolDiscovery(
  state: OrdinaryMcpToolDiscoveryState,
  serverNames: string[],
): OrdinaryMcpToolDiscoveryState {
  const serverSetKey = buildOrdinaryMcpServerSetKey(serverNames);
  if (state.serverSetKey === serverSetKey) return state;
  return {
    generation: state.generation,
    serverSetKey,
    toolsByServer: {},
    toolsLoading: false,
    toolsUnavailable: false,
  };
}

/** Read-only MCP directory for ordinary company accounts. */
export function OrdinaryMcpCatalog({
  servers,
  isLoading,
  listError,
}: OrdinaryMcpCatalogProps) {
  const { t } = useTranslation();
  const discoveryGenerationRef = useRef(0);
  const [toolDiscovery, setToolDiscovery] =
    useState<OrdinaryMcpToolDiscoveryState>(() =>
      beginOrdinaryMcpToolDiscovery(0, []),
    );
  const serverNames = useMemo(
    () => Array.from(new Set(servers.map((server) => server.name))).sort(),
    [servers],
  );

  useEffect(() => {
    const generation = discoveryGenerationRef.current + 1;
    discoveryGenerationRef.current = generation;
    setToolDiscovery(beginOrdinaryMcpToolDiscovery(generation, serverNames));
    const isCurrentGeneration = () =>
      discoveryGenerationRef.current === generation;
    const invalidateGeneration = () => {
      if (isCurrentGeneration()) discoveryGenerationRef.current += 1;
    };
    if (serverNames.length === 0) return invalidateGeneration;

    void collectOrdinaryMcpTools(serverNames, async (serverName) => {
      const response = await mcpApi.discoverTools(serverName);
      return response.tools;
    }, isCurrentGeneration)
      .then((result) => {
        if (discoveryGenerationRef.current !== generation) return;
        setToolDiscovery((current) =>
          publishOrdinaryMcpToolDiscovery(current, generation, result),
        );
      })
      .catch(() => {
        if (discoveryGenerationRef.current !== generation) return;
        setToolDiscovery((current) =>
          publishOrdinaryMcpToolDiscovery(current, generation, {
            toolsByServer: {},
            unavailable: true,
          }),
        );
      });
    return invalidateGeneration;
  }, [serverNames]);

  const { toolsByServer, toolsLoading, toolsUnavailable } =
    resolveVisibleOrdinaryMcpToolDiscovery(toolDiscovery, serverNames);

  const catalog = servers
    .map((server) =>
      projectOrdinaryMcpCatalogItem({
        name: server.name,
        tools: toolsByServer[server.name] ?? [],
      }),
    )
    .filter((server) => server.name.length > 0);

  return (
    <div className={workbenchSurface.page} data-ordinary-mcp-catalog>
      <PanelHeader
        title={t("mcp.available.title")}
        subtitle={t("mcp.available.subtitle")}
        icon={<Server size={20} className="text-theme-text-secondary" />}
      />
      <div className={workbenchSurface.catalog.content}>
        {isLoading || toolsLoading ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("mcp.available.loading")}
            </p>
          </div>
        ) : listError || toolsUnavailable ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("mcp.available.unavailable")}
            </p>
          </div>
        ) : catalog.length === 0 ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("mcp.available.empty")}
            </p>
          </div>
        ) : (
          <div className={workbenchSurface.catalog.cardGrid}>
            {catalog.map((server) => (
              <article
                key={server.name}
                className={workbenchSurface.catalog.entryCard}
              >
                <div className="flex items-center gap-2">
                  <Server size={16} className="text-theme-text-secondary" />
                  <h2 className={workbenchSurface.catalog.title}>{server.name}</h2>
                </div>
                {server.tools.length === 0 ? (
                  <p className={`mt-3 ${workbenchSurface.catalog.body}`}>
                    {t("mcp.card.noTools")}
                  </p>
                ) : (
                  <ul className="mt-3 space-y-3">
                    {server.tools.map((tool) => (
                      <li key={tool.name} className="rounded-md bg-[var(--theme-bg-sidebar)] p-3">
                        <div className="flex items-center gap-2">
                          <Wrench size={14} className="text-theme-text-secondary" />
                          <h3 className="text-sm font-medium text-[var(--theme-text)]">
                            {tool.name}
                          </h3>
                        </div>
                        {tool.description ? (
                          <p className={`mt-1 ${workbenchSurface.catalog.body}`}>
                            {tool.description}
                          </p>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                )}
              </article>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
