import { Server, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MCPServerResponse } from "../../types";
import type { useMcpToolDiscovery } from "../mcp/useMcpToolDiscovery";
import { PanelHeader } from "../common/PanelHeader";
import { workbenchSurface } from "../workbench/workbenchSurface";
import { projectOrdinaryMcpCatalogItem } from "./ordinaryCatalogPolicy";

interface OrdinaryMcpCatalogProps {
  servers: MCPServerResponse[];
  isLoading: boolean;
  listError: string | null;
  discovery: ReturnType<typeof useMcpToolDiscovery>;
  onRetry?: () => void;
}

/** Read-only MCP directory for ordinary company accounts. */
export function OrdinaryMcpCatalog({
  servers,
  isLoading,
  listError,
  discovery,
  onRetry,
}: OrdinaryMcpCatalogProps) {
  const { t } = useTranslation();
  const catalog = servers
    .map((server) =>
      projectOrdinaryMcpCatalogItem({
        name: server.name,
        tools: discovery.toolsByServer.get(server.name) ?? [],
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
        {isLoading ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("mcp.available.loading")}
            </p>
          </div>
        ) : listError ? (
          <div className={workbenchSurface.catalog.emptyState}>
            <p className={workbenchSurface.catalog.emptyTitle}>
              {t("mcp.available.unavailable")}
            </p>
            {listError && onRetry ? <button type="button" className="btn-secondary mt-3" data-mcp-retry onClick={onRetry}>{t("mcp.catalogUnavailable.retry")}</button> : null}
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
                  {discovery.statusByServer.get(server.name) === "ready" ? <span className="ml-auto text-xs text-[var(--theme-text-secondary)]">{t("mcp.tools.count", { count: server.tools.length })}</span> : null}
                </div>
                {discovery.statusByServer.get(server.name) !== "ready" ? (
                  <div className="mt-3 text-sm text-[var(--theme-text-secondary)]">
                    <p>{t(discovery.statusByServer.get(server.name) === "loading" ? "mcp.available.loading" : "mcp.available.unavailable")}</p>
                    {discovery.statusByServer.get(server.name) === "unavailable" ? <button type="button" className="btn-secondary mt-3" onClick={discovery.refresh}>{t("mcp.tools.retry")}</button> : null}
                  </div>
                ) : server.tools.length === 0 ? (
                  <p className={`mt-3 ${workbenchSurface.catalog.body}`}>
                    {t("mcp.tools.empty")}
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
