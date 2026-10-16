import { useEffect, useId, useRef, useState } from "react";
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
}

export function MCPToolDrawer({ serverName, fullCatalog = false, status, tools, onRefresh, onClose }: MCPToolDrawerProps) {
  const { t } = useTranslation();
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [search, setSearch] = useState("");
  useEffect(() => {
    const dialog = dialogRef.current!;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    return () => {
      if (dialog.open) dialog.close();
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, []);
  const query = search.trim().toLowerCase();
  const visibleTools = tools.filter((tool) => `${tool.name} ${tool.description}`.toLowerCase().includes(query));
  return (
    <dialog ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby={titleId} data-mcp-tool-detail
      onCancel={(event) => { event.preventDefault(); onClose(); }}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled])"));
        const first = controls[0];
        const last = controls.at(-1);
        // Native modal tabbing can otherwise move focus into browser chrome.
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
      className="fixed inset-y-0 left-auto m-0 h-dvh max-h-none w-full max-w-xl border-0 border-l border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-0 text-[var(--theme-text)] shadow-xl backdrop:bg-[var(--theme-overlay-strong)]">
      <div className="flex h-full min-h-0 flex-col">
        <header className="flex items-start justify-between gap-3 border-b border-[var(--theme-border)] p-5">
          <div className="min-w-0">
            <h2 id={titleId} className="break-words text-base font-semibold">{t("mcp.tools.title", { name: serverName })}</h2>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{t(fullCatalog ? "mcp.tools.catalogScope" : "mcp.tools.accountScope")}</p>
          </div>
          <button type="button" className="btn-icon min-h-11 min-w-11" data-mcp-tool-detail-close aria-label={t("common.close")} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="flex items-center justify-between gap-3 px-5 py-3">
          <span className="text-sm" role="status">{status === "ready" ? t("mcp.tools.count", { count: tools.filter((tool) => tool.available !== false).length }) : t(`mcp.discovery.${status}`)}</span>
          <button type="button" className="btn-secondary inline-flex min-h-11 items-center gap-2" data-mcp-refresh-tools onClick={onRefresh} disabled={status !== "ready" && status !== "unavailable"}><RefreshCw size={16} />{t("mcp.tools.refresh")}</button>
        </div>
        {status === "ready" ? (
          <div className="relative mx-5 mb-3">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--theme-text-secondary)]" />
            <input type="search" className="panel-search h-10" value={search} onChange={(event) => setSearch(event.target.value)} aria-label={t("mcp.tools.search")} placeholder={t("mcp.tools.search")} />
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
    </dialog>
  );
}
