import { useEffect, useMemo, useState } from "react";
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

/** Read-only MCP directory for ordinary company accounts. */
export function OrdinaryMcpCatalog({
  servers,
  isLoading,
  listError,
}: OrdinaryMcpCatalogProps) {
  const { t } = useTranslation();
  const [toolsByServer, setToolsByServer] = useState<Record<string, MCPToolInfo[]>>(
    {},
  );
  const [toolsLoading, setToolsLoading] = useState(false);
  const [toolsUnavailable, setToolsUnavailable] = useState(false);
  const serverNames = useMemo(
    () => servers.map((server) => server.name).sort(),
    [servers],
  );

  useEffect(() => {
    if (serverNames.length === 0) {
      setToolsByServer({});
      setToolsUnavailable(false);
      return;
    }

    let cancelled = false;
    setToolsLoading(true);
    setToolsUnavailable(false);
    void Promise.all(
      serverNames.map(async (serverName) => {
        try {
          const response = await mcpApi.discoverTools(serverName);
          return [serverName, response.tools] as const;
        } catch {
          return [serverName, null] as const;
        }
      }),
    ).then((results) => {
      if (cancelled) return;
      const nextTools: Record<string, MCPToolInfo[]> = {};
      let unavailable = false;
      for (const [serverName, tools] of results) {
        if (tools === null) {
          unavailable = true;
          continue;
        }
        nextTools[serverName] = tools;
      }
      setToolsByServer(nextTools);
      setToolsUnavailable(unavailable);
      setToolsLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [serverNames]);

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
