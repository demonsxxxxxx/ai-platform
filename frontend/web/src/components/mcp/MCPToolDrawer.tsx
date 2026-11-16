import { Button, Checkbox, Input, DrawerRoot, DrawerPortal, DrawerBackdrop, DrawerPopup, DrawerHeader, DrawerTitle } from "@lobehub/ui/base-ui";
import { useId, useMemo, useState, type RefObject } from "react";
import { ChevronRight, RefreshCw, Search, Wrench, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { MCPToolInfo } from "../../types";
import type { McpToolDiscoveryStatus } from "./useMcpToolDiscovery";
import { groupKeyForTool, groupLabelForTool } from "../selectors/toolGrouping";

interface MCPToolDrawerProps {
  serverName: string;
  fullCatalog?: boolean;
  status: McpToolDiscoveryStatus;
  tools: MCPToolInfo[];
  defaultToolIds?: string[];
  onSaveDefaults?: (toolIds: string[]) => Promise<unknown>;
  savingDefaults?: boolean;
  defaultsError?: string | null;
  onRefresh: () => void;
  onClose: () => void;
  returnFocusRef: RefObject<HTMLElement | null>;
}

export function MCPToolDrawer({ serverName, fullCatalog = false, status, tools, defaultToolIds = [], onSaveDefaults, savingDefaults = false, defaultsError, onRefresh, onClose, returnFocusRef }: MCPToolDrawerProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const [search, setSearch] = useState("");
  const [selectedDefaults, setSelectedDefaults] = useState(() => new Set(defaultToolIds));
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(() => new Set());
  const dirty = selectedDefaults.size !== defaultToolIds.length || defaultToolIds.some((id) => !selectedDefaults.has(id));
  const toggleDefault = (toolId: string, checked: boolean) => setSelectedDefaults((previous) => {
    const next = new Set(previous);
    if (checked) next.add(toolId); else next.delete(toolId);
    return next;
  });
  const availableTools = tools.filter((tool) => tool.available !== false);
  const allDefaultsSelected = availableTools.length > 0 && availableTools.every((tool) => selectedDefaults.has(tool.name));
  const missingDefaults = status === "ready" || status === "unavailable"
    ? [...selectedDefaults].filter((id) => !tools.some((tool) => tool.name === id)) : [];
  const query = search.trim().toLowerCase();
  const groups = useMemo(() => {
    const bySource = new Map<string, { key: string; label: string | null; tools: MCPToolInfo[] }>();
    for (const tool of tools) {
      const source = { ...tool, category: "mcp" as const, server: serverName };
      const key = groupKeyForTool(source);
      const existing = bySource.get(key);
      if (existing) existing.tools.push(tool);
      else bySource.set(key, { key, label: groupLabelForTool(source), tools: [tool] });
    }
    return [...bySource.values()].sort((a, b) => (a.label ?? "").localeCompare(b.label ?? ""));
  }, [serverName, tools]);
  const visibleGroups = groups.flatMap((group) => {
    const label = group.label ?? t("tools.unknownSource");
    const matchesSource = `${label} ${serverName}`.toLowerCase().includes(query);
    const visibleTools = group.tools.filter((tool) => matchesSource || `${tool.name} ${tool.description}`.toLowerCase().includes(query));
    return visibleTools.length > 0 ? [{ ...group, label, visibleTools }] : [];
  });
  const toggleGroup = (groupTools: MCPToolInfo[], checked: boolean) => setSelectedDefaults((previous) => {
    const next = new Set(previous);
    for (const tool of groupTools) {
      if (!checked) next.delete(tool.name);
      else if (tool.available !== false) next.add(tool.name);
    }
    return next;
  });
  return (
    <DrawerRoot open onOpenChange={(open) => { if (!open && !savingDefaults) onClose(); }}>
      <DrawerPortal>
        <DrawerBackdrop />
        <DrawerPopup finalFocus={returnFocusRef} placement="right" width="min(36rem, 100vw)" aria-labelledby={titleId} data-mcp-tool-detail>
      <div className="flex h-full min-h-0 flex-col">
        <DrawerHeader>
          <div className="min-w-0">
            <DrawerTitle id={titleId}>{t("mcp.tools.title", { name: serverName })}</DrawerTitle>
            <p className="mt-1 text-xs text-[var(--theme-text-secondary)]">{t(fullCatalog ? "mcp.tools.catalogScope" : "mcp.tools.accountScope")}</p>
          </div>
          <Button type="text" htmlType="button" size="large" data-mcp-tool-detail-close autoFocus aria-label={t("common.close")} disabled={savingDefaults} onClick={onClose} icon={<X size={18} />} />
        </DrawerHeader>
        <div className="flex items-center justify-between gap-3 px-5 py-3">
          <span className="text-sm" role="status">{status === "ready" ? t("mcp.tools.count", { count: tools.filter((tool) => tool.available !== false).length }) : t(`mcp.discovery.${status}`)}</span>
          <Button type="default" htmlType="button" size="large" icon={<RefreshCw size={16} />} data-mcp-refresh-tools onClick={onRefresh} disabled={status !== "ready" && status !== "unavailable"}>{t("mcp.tools.refresh")}</Button>
        </div>
        {status === "ready" ? (
          <div className="mx-5 mb-3">
            <Input type="search" size="large" prefix={<Search size={16} />} value={search} onChange={(event) => { setSearch(event.target.value); setExpandedGroups(event.target.value.trim() ? new Set(groups.map((group) => group.key)) : new Set()); }} aria-label={t("mcp.tools.search")} placeholder={t("mcp.tools.search")} />
            {onSaveDefaults ? <div className="mt-3"><Checkbox checked={allDefaultsSelected} disabled={savingDefaults || availableTools.length === 0} onChange={(checked) => setSelectedDefaults((previous) => {
              const next = new Set(previous);
              for (const tool of availableTools) { if (checked) next.add(tool.name); else next.delete(tool.name); }
              return next;
            })}>默认启用全部可用工具</Checkbox></div> : null}
          </div>
        ) : null}
        <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5" aria-busy={status === "loading"}>
          {status !== "ready" ? <p className="py-4 text-sm text-[var(--theme-text-secondary)]">{t(`mcp.discovery.${status}`)}</p> : visibleGroups.length === 0 ? (
            <p className="py-4 text-sm text-[var(--theme-text-secondary)]">{t(query ? "mcp.tools.noMatches" : fullCatalog ? "mcp.tools.catalogEmpty" : "mcp.tools.empty")}</p>
          ) : (
            <div className="space-y-2">
              {visibleGroups.map((group, index) => {
                const expanded = expandedGroups.has(group.key);
                const selectable = group.tools.filter((tool) => tool.available !== false);
                const selected = group.tools.filter((tool) => selectedDefaults.has(tool.name)).length;
                const selectedAvailable = selectable.filter((tool) => selectedDefaults.has(tool.name)).length;
                const checked = selectable.length > 0 && selectedAvailable === selectable.length;
                const contentId = `${titleId}-source-${index}`;
                return <section key={group.key} data-mcp-tool-group={group.key} className="overflow-hidden rounded-lg border border-[var(--theme-border)]">
                  <div className="flex items-center gap-2 bg-[var(--theme-bg-sidebar)] px-2 py-1">
                    <div className="min-w-0 flex-1"><Button type="text" htmlType="button" block aria-expanded={expanded} aria-controls={contentId} aria-label={`${expanded ? "收起" : "展开"} ${group.label}`} onClick={() => setExpandedGroups((previous) => {
                      const next = new Set(previous);
                      if (next.has(group.key)) next.delete(group.key); else next.add(group.key);
                      return next;
                    })}><span className="flex w-full min-w-0 items-center gap-2 text-left"><ChevronRight size={14} className={`shrink-0 text-[var(--theme-text-secondary)] transition-transform ${expanded ? "rotate-90" : ""}`} /><span className="min-w-0 flex-1 truncate text-sm font-semibold">{group.label}</span><span className="shrink-0 text-xs tabular-nums text-[var(--theme-text-secondary)]">{selected}/{group.tools.length}</span></span></Button></div>
                    {onSaveDefaults ? <Checkbox checked={checked} indeterminate={selected > 0 && !checked} disabled={savingDefaults || (selectable.length === 0 && selected === 0)} onChange={() => toggleGroup(group.tools, selectable.length > 0 && !checked)} aria-label={`默认启用分组 ${group.label}`} /> : null}
                  </div>
                  {expanded ? <ul id={contentId} className="divide-y divide-[var(--theme-border)] px-3">
                    {group.visibleTools.map((tool) => <li key={tool.name} data-mcp-tool-row={tool.name} className="py-3 pl-4">
                      <div className="flex items-start gap-2"><Wrench size={14} className="mt-0.5 shrink-0 text-[var(--theme-text-secondary)]" /><h3 className="min-w-0 flex-1 break-all text-sm font-medium">{tool.name}</h3>{onSaveDefaults ? <Checkbox checked={selectedDefaults.has(tool.name)} disabled={savingDefaults || (tool.available === false && !selectedDefaults.has(tool.name))} onChange={(checked) => toggleDefault(tool.name, checked === true)} aria-label={`默认启用 ${tool.name}`} /> : null}</div>
                      {tool.available === false ? <p className="mt-2 text-sm text-[var(--theme-warning)]">{t("mcp.catalog.missing")}</p> : null}
                      {tool.description ? <p className="mt-1 whitespace-pre-wrap break-words text-sm text-[var(--theme-text-secondary)]">{tool.description}</p> : null}
                    </li>)}
                  </ul> : null}
                </section>;
              })}
            </div>
          )}
          {onSaveDefaults && missingDefaults.length > 0 ? <div className="mt-3 space-y-2"><p className="text-sm text-[var(--theme-text-secondary)]">目录中未找到的默认工具</p>{missingDefaults.map((id) => <div key={id}><Checkbox checked disabled={savingDefaults} onChange={() => toggleDefault(id, false)} aria-label={`移除默认 ${id}`}><span className="break-all text-sm">{id}</span></Checkbox></div>)}</div> : null}
        </div>
        {onSaveDefaults ? <div className="shrink-0 border-t border-[var(--theme-border)] px-5 py-4">
          <p className="mb-3 text-xs text-[var(--theme-text-secondary)]">默认加入普通对话与专家的新任务，仍按使用者权限过滤。</p>
          {defaultsError ? <p role="alert" className="mb-3 text-sm text-[var(--theme-danger-text)]">{defaultsError}</p> : null}
          <div className="flex items-center justify-between gap-3"><span className="text-sm">已选 {selectedDefaults.size} 个默认工具</span><Button htmlType="button" type="primary" loading={savingDefaults} disabled={!dirty || savingDefaults} onClick={() => { void onSaveDefaults([...selectedDefaults]); }}>保存默认工具</Button></div>
        </div> : null}
      </div>
        </DrawerPopup>
      </DrawerPortal>
    </DrawerRoot>
  );
}
