import { Button, Input, DrawerRoot, DrawerPortal, DrawerBackdrop, DrawerPopup, DrawerHeader, DrawerTitle } from "@lobehub/ui/base-ui";
import { useId, useState, type RefObject } from "react";
import { RefreshCw, Search, Wrench, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MCPToolInfo } from "../../types";
import type { McpToolDiscoveryStatus } from "./useMcpToolDiscovery";

interface MCPToolDrawerProps {
  serverName: string;
  fullCatalog?: boolean;
  status: McpToolDiscoveryStatus;
  tools: MCPToolInfo[];
  onRefresh: () => void;
  onClose: () => void;
  returnFocusRef: RefObject<HTMLElement | null>;
}

export function MCPToolDrawer({ serverName, fullCatalog = false, status, tools, onRefresh, onClose, returnFocusRef }: MCPToolDrawerProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const [search, setSearch] = useState("");
  const query = search.trim().toLowerCase();
  const visibleTools = tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(query));
  return (
    <DrawerRoot open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DrawerPortal>
        <DrawerBackdrop />
        <DrawerPopup finalFocus={returnFocusRef} placement="right" width="min(36rem, 100vw)" aria-labelledby={titleId} data-mcp-tool-detail>
      <div className="flex h-full min-h-0 flex-col">
        <DrawerHeader>
          <div className="min-w-0">
            <DrawerTitle id={titleId}>{t("mcp.tools.title", { name: serverName })}</DrawerTitle>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{t(fullCatalog ? "mcp.tools.catalogScope" : "mcp.tools.accountScope")}</p>
          </div>
          <Button type="text" htmlType="button" size="large" data-mcp-tool-detail-close autoFocus aria-label={t("common.close")} onClick={onClose} icon={<X size={18} />} />
        </DrawerHeader>
        <div className="flex items-center justify-between gap-3 px-5 py-3">
          <span className="text-sm" role="status">{status === "ready" ? t("mcp.tools.count", { count: tools.filter((tool) => tool.available !== false).length }) : t(`mcp.discovery.${status}`)}</span>
          <Button type="default" htmlType="button" size="large" icon={<RefreshCw size={16} />} data-mcp-refresh-tools onClick={onRefresh} disabled={status !== "ready" && status !== "unavailable"}>{t("mcp.tools.refresh")}</Button>
        </div>
        {status === "ready" ? (
          <div className="mx-5 mb-3">
            <Input type="search" size="large" prefix={<Search size={16} />} value={search} onChange={(event) => setSearch(event.target.value)} aria-label={t("mcp.tools.search")} placeholder={t("mcp.tools.search")} />
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5" aria-busy={status === "loading"}>
          {status !== "ready" ? <p className="py-4 text-sm text-[var(--theme-text-secondary)]">{t(`mcp.discovery.${status}`)}</p> : visibleTools.length === 0 ? (
            <p className="py-4 text-sm text-[var(--theme-text-secondary)]">{t(query ? "mcp.tools.noMatches" : fullCatalog ? "mcp.tools.catalogEmpty" : "mcp.tools.empty")}</p>
          ) : (
            <ul className="space-y-3">
              {visibleTools.map((tool) => <li key={tool.name} className="rounded-lg border border-[var(--theme-border)] p-3">
                <div className="flex items-start gap-2"><Wrench size={16} className="mt-0.5 shrink-0 text-[var(--theme-text-secondary)]" /><h3 className="break-words text-sm font-medium">{tool.name}</h3></div>
                {tool.available === false ? <p className="mt-2 text-sm text-[var(--theme-warning)]">{t("mcp.catalog.missing")}</p> : null}
                {tool.description ? <p className="mt-2 whitespace-pre-wrap break-words text-sm text-[var(--theme-text-secondary)]">{tool.description}</p> : null}
              </li>)}
            </ul>
          )}
        </div>
      </div>
        </DrawerPopup>
      </DrawerPortal>
    </DrawerRoot>
  );
}
