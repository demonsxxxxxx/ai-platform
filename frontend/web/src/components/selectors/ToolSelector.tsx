import { Button, Input, ModalRoot, ModalPortal, ModalBackdrop, ModalPopup, ModalTitle } from "@lobehub/ui/base-ui";
import { Table } from "@lobehub/ui/base-ui";
/* eslint-disable react-refresh/only-export-components -- catalog notice mapping is tested with this selector */
import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Wrench,
  ChevronRight,
  Globe,
  Bot,
  MessageCircle,
  Terminal,
  Container,
  X,
  Info,
  Plus,
} from "lucide-react";
import { Checkbox } from "@lobehub/ui/base-ui";
import type { ToolState, ToolCategory } from "../../types";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";
import {
  canSelectChatMcpTools,
  ChatMcpCatalogContext,
  type ChatMcpCatalogState,
} from "../../hooks/useTools";

interface ToolSelectorProps {
  tools: ToolState[];
  onToggleTool: (toolName: string) => void;
  onToggleCategory: (category: ToolCategory, enabled: boolean) => void;
  onToggleAll: (enabled: boolean) => void;
  enabledCount: number;
  totalCount: number;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  searchSeed?: string;
}

type LabeledToolState = ToolState & { label?: string };

function publicToolLabel(tool: ToolState): string {
  return (tool as LabeledToolState).label || tool.name;
}

const categoryIcons: Record<ToolCategory, typeof Bot> = {
  builtin: Terminal,
  skill: Bot,
  human: MessageCircle,
  mcp: Globe,
  sandbox: Container,
};

export interface ChatMcpCatalogNotice {
  titleKey: string;
  detailKeys: string[];
  retryable: boolean;
}

/** Map bounded catalog state to localized, public-safe selector copy. */
export function getChatMcpCatalogNotice(
  catalogState: ChatMcpCatalogState,
): ChatMcpCatalogNotice | null {
  switch (catalogState.status) {
    case "loading":
      return { titleKey: "tools.catalog.loading", detailKeys: [], retryable: false };
    case "empty":
      return { titleKey: "tools.catalog.empty", detailKeys: [], retryable: false };
    case "degraded":
      return {
        titleKey: "tools.catalog.degraded",
        detailKeys: [...new Set(catalogState.unavailable)].slice(0, 3),
        retryable: false,
      };
    case "error":
      return { titleKey: "tools.catalog.error", detailKeys: [], retryable: true };
    case "ready":
      return null;
  }
}

export function ToolSelector({
  tools,
  onToggleTool,
  onToggleCategory,
  onToggleAll,
  enabledCount,
  totalCount,
  isOpen: externalIsOpen,
  onOpenChange: externalOnOpenChange,
  searchSeed,
}: ToolSelectorProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { catalogState, retryTools } = useContext(ChatMcpCatalogContext);
  const catalogNotice = getChatMcpCatalogNotice(catalogState);
  const catalogAllowsSelection = canSelectChatMcpTools(catalogState.status);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [internalOpen, setInternalOpen] = useState(false);
  const isOpen = externalIsOpen ?? internalOpen;
  const setIsOpen = externalOnOpenChange ?? setInternalOpen;
  const [expandedTools, setExpandedTools] = useState<Set<string>>(new Set());
  const [expandedCategories, setExpandedCategories] = useState<
    Set<ToolCategory>
  >(new Set(["mcp"]));
  const [searchQuery, setSearchQuery] = useState("");
  const swipeRef = useSwipeToClose({
    onClose: () => setIsOpen(false),
    enabled: isOpen,
  });

  useEffect(() => {
    if (!isOpen || searchSeed === undefined) return;
    setSearchQuery(searchSeed);
  }, [isOpen, searchSeed]);

  // 按类别分组，每组内按工具名称排序 - 使用 useMemo 缓存计算结果
  const filteredTools = useMemo(() => {
    const normalized = searchQuery.trim().toLowerCase();
    if (!normalized) return tools;
    return tools.filter(
      (tool) =>
        tool.name.toLowerCase().includes(normalized) ||
        publicToolLabel(tool).toLowerCase().includes(normalized) ||
        tool.description.toLowerCase().includes(normalized) ||
        tool.category.toLowerCase().includes(normalized) ||
        (tool.server ?? "").toLowerCase().includes(normalized),
    );
  }, [searchQuery, tools]);

  const groupedTools = useMemo(
    () =>
      filteredTools.reduce(
        (acc, tool) => {
          if (!acc[tool.category]) {
            acc[tool.category] = [];
          }
          acc[tool.category].push(tool);
          return acc;
        },
        {} as Record<ToolCategory, ToolState[]>,
      ),
    [filteredTools],
  );

  // Sort tools within each category by name
  const sortedGroupedTools = useMemo(() => {
    const sorted: Record<string, ToolState[]> = {};
    for (const [category, categoryTools] of Object.entries(groupedTools)) {
      sorted[category] = [...categoryTools].sort((a, b) =>
        publicToolLabel(a)
          .toLowerCase()
          .localeCompare(publicToolLabel(b).toLowerCase()),
      );
    }
    return sorted;
  }, [groupedTools]);

  const toggleToolExpand = (toolName: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setExpandedTools((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(toolName)) {
        newSet.delete(toolName);
      } else {
        newSet.add(toolName);
      }
      return newSet;
    });
  };

  const toggleCategoryExpand = (category: ToolCategory) => {
    setExpandedCategories((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(category)) {
        newSet.delete(category);
      } else {
        newSet.add(category);
      }
      return newSet;
    });
  };

  const handleToolToggle = (tool: ToolState) => {
    if (!catalogAllowsSelection || tool.system_disabled) return;
    onToggleTool(tool.name);
  };

  // Keep the modal subtree mounted when search or expansion state changes.
  const modalContent = (
    <div
      ref={(node) => { swipeRef.current = node; }}
      className="flex min-h-[40vh] max-h-[85dvh] w-full flex-col overflow-hidden sm:min-h-0 sm:max-h-[80vh]"
      data-composer-mcp-selector
    >
      {/* Header */}
      <div
        className="flex items-center justify-between px-4 sm:px-5 py-3 sm:py-4 border-b"
        style={{ borderColor: "var(--theme-border)" }}
      >
        {/* Mobile drag handle */}
        <div className="absolute left-1/2 -translate-x-1/2 top-2 w-10 h-1 rounded-full bg-[var(--theme-border)] sm:hidden" />
        <div className="flex items-center gap-3 mt-2 sm:mt-0">
          <div className="size-9 sm:size-10 rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)] flex items-center justify-center">
            <Wrench
              size={16}
              className="text-[var(--theme-text-secondary)] sm:w-[18px] sm:h-[18px]"
            />
          </div>
          <div>
            <ModalTitle>{t("tools.title")}</ModalTitle>
            <p className="text-xs sm:text-xs text-[var(--theme-text-secondary)]">
              {t("tools.selected", {
                enabled: enabledCount,
                total: totalCount,
              })}
            </p>
          </div>
        </div>
        <Button type="text"
          aria-label={t("common.close")}
          onClick={() => setIsOpen(false)}
          className="p-2 rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] active:bg-[var(--theme-bg-sidebar)] transition-colors"
        >
          <X size={18} />
        </Button>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-2 px-4 sm:px-5 py-2 sm:py-2.5 border-b border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)]">
        <Button type="text"
          htmlType="button"
          disabled={!catalogAllowsSelection}
          onClick={() => onToggleAll(true)}
          className="px-3 py-2 sm:py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-workbench-panel)] active:bg-[var(--theme-workbench-panel)] rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("tools.selectAll")}
        </Button>
        <div className="w-px h-4 bg-[var(--theme-border)]" />
        <Button type="text"
          htmlType="button"
          disabled={!catalogAllowsSelection}
          onClick={() => onToggleAll(false)}
          className="px-3 py-2 sm:py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-workbench-panel)] active:bg-[var(--theme-workbench-panel)] rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-50"
        >
          {t("tools.deselectAll")}
        </Button>
        <div className="flex-1" />
        <Button type="text"
          htmlType="button"
          onClick={() => {
            setIsOpen(false);
            navigate("/mcp");
          }}
          className="flex items-center gap-1 px-3 py-2 sm:py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] hover:text-[var(--theme-text)] hover:bg-[var(--theme-bg-sidebar)] rounded-lg transition-colors"
        >
          <Plus size={14} />
          <span>{t("tools.add")}</span>
        </Button>
      </div>

      <div className="border-b border-[var(--theme-border)] bg-[var(--theme-bg)] px-4 py-3 sm:px-5">
        <Input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          autoFocus
          aria-label={t("tools.searchPlaceholder", "Search tools")}
          placeholder={t("tools.searchPlaceholder", "Search tools")}
          className="h-10 w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm text-[var(--theme-text)] outline-none transition-colors placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary-light)]"
        />
      </div>

      {/* Categories */}
      <div className="flex-1 overflow-y-auto p-2.5 sm:p-3 space-y-1.5">
        {catalogNotice && (
          <div
            aria-live="polite"
            className="rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] p-5 text-center"
            data-mcp-catalog-status={catalogState.status}
          >
            <p className="text-sm font-medium text-[var(--theme-text)]">
              {t(catalogNotice.titleKey)}
            </p>
            {catalogNotice.detailKeys.map((detailKey) => (
              <p
                key={detailKey}
                className="mt-1 text-xs text-[var(--theme-text-secondary)]"
              >
                {t(detailKey)}
              </p>
            ))}
            {catalogNotice.retryable && retryTools ? (
              <Button type="text"
                htmlType="button"
                onClick={retryTools}
                className="mt-3 px-3 py-2 text-xs font-medium text-[var(--theme-primary)] hover:bg-[var(--theme-bg-sidebar)] rounded-lg transition-colors"
              >
                {t("tools.catalog.retry")}
              </Button>
            ) : null}
          </div>
        )}
        {Object.entries(sortedGroupedTools).map(
          ([category, categoryTools]: [string, ToolState[]]) => {
            const cat = category as ToolCategory;
            const Icon = categoryIcons[cat];
            const enabledInCategory = categoryTools.filter(
              (t: ToolState) => t.enabled,
            ).length;
            const allEnabled = enabledInCategory === categoryTools.length;
            const isExpanded = expandedCategories.has(cat);

            return (
              <div
                key={category}
                className="rounded-lg border border-[var(--theme-border)] overflow-hidden bg-[var(--theme-workbench-panel)]"
              >
                {/* Category Header */}
                <div
                  className="flex items-center gap-2 sm:gap-2.5 px-3 sm:px-3.5 py-2.5 cursor-pointer hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)] transition-all duration-200"
                  onClick={() => toggleCategoryExpand(cat)}
                >
                  <ChevronRight
                    size={16}
                    className={`text-[var(--theme-text-secondary)] transition-transform duration-200 ease-out ${
                      isExpanded ? "rotate-90" : ""
                    }`}
                  />
                  <div className="w-6 h-6 sm:w-7 sm:h-7 rounded-lg bg-[var(--theme-bg-sidebar)] flex items-center justify-center ring-1 ring-[var(--theme-border)]">
                    <Icon
                      size={13}
                      className="text-[var(--theme-text-secondary)] sm:w-[14px] sm:h-[14px]"
                    />
                  </div>
                  <div className="flex-1 min-w-0">
                    <span className="text-[13px] sm:text-sm font-medium text-[var(--theme-text)]">
                      {t(`tools.categories.${cat}`)}
                    </span>
                    <span className="ml-1.5 sm:ml-2 text-xs sm:text-xs text-[var(--theme-text-secondary)] tabular-nums">
                      {enabledInCategory}/{categoryTools.length}
                    </span>
                  </div>
                  <Checkbox
                    checked={allEnabled}
                    onChange={() => onToggleCategory(cat, !allEnabled)}
                    disabled={
                      !catalogAllowsSelection ||
                      categoryTools.every((tool) => tool.system_disabled)
                    }
                  onClick={(event) => event.stopPropagation()} />
                </div>

                {/* Tools List */}
                {isExpanded && (
                  <div className="animate-[fade-in_150ms_ease-out]">
                    <div className="px-1 sm:px-2 pb-2 pt-1 space-y-0.5">
                      {categoryTools.map((tool: ToolState) => {
                        const isToolExpanded = expandedTools.has(tool.name);
                        const hasParams =
                          tool.parameters && tool.parameters.length > 0;

                        return (
                          <div key={tool.name} className="group">
                            {/* Tool Row */}
                            <div
                              className={`flex items-center gap-1.5 sm:gap-2 px-2 sm:px-2.5 py-2 sm:py-2 rounded-lg cursor-pointer transition-all duration-150 ${
                                tool.system_disabled
                                  ? "cursor-not-allowed bg-[var(--theme-danger-soft)] opacity-70"
                                  : tool.enabled
                                  ? "hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)]"
                                  : "bg-[var(--theme-primary)]/[0.06] hover:bg-[var(--theme-primary)]/[0.12] active:bg-[var(--theme-primary)]/[0.18]"
                              }`}
                              data-composer-mcp-row={tool.name}
                              data-composer-mcp-state={
                                tool.system_disabled
                                  ? "denied"
                                  : tool.enabled
                                    ? "enabled"
                                    : "disabled"
                              }
                              onClick={() => handleToolToggle(tool)}
                              aria-disabled={tool.system_disabled || undefined}
                            >
                              {/* Expand button for tools with params */}
                              <Button type="text"
                                onClick={(e) => toggleToolExpand(tool.name, e)}
                                className={`p-1 -ml-1 rounded transition-all duration-200 touch-manip ${
                                  hasParams
                                    ? "hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)]"
                                    : ""
                                }`}
                              >
                                {hasParams ? (
                                  <ChevronRight
                                    size={14}
                                    className={`text-[var(--theme-text-secondary)] transition-transform duration-200 ease-out ${
                                      isToolExpanded ? "rotate-90" : ""
                                    }`}
                                  />
                                ) : (
                                  <div className="w-[14px] h-[14px]" />
                                )}
                              </Button>

                              <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-1.5 sm:gap-2 flex-wrap">
                                  <span
                                    className={`text-[12px] sm:text-[13px] font-medium truncate ${
                                      tool.enabled
                                        ? "text-[var(--theme-text)]"
                                        : "text-[var(--theme-primary)]"
                                    }`}
                                  >
                                    {publicToolLabel(tool)}
                                  </span>
                                  {tool.server && (
                                    <span className="text-[9px] sm:text-xs px-1.5 py-0.5 rounded-md bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] font-medium">
                                      {tool.server}
                                    </span>
                                  )}
                                  {tool.system_disabled && (
                                    <span className="text-[9px] sm:text-xs px-1.5 py-0.5 rounded-md bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)] ring-1 ring-[var(--theme-danger-ring)] font-medium">
                                      {t("tools.systemDisabled")}
                                    </span>
                                  )}
                                </div>
                                <p className="text-xs sm:text-xs text-[var(--theme-text-secondary)] truncate mt-0.5 leading-relaxed text-left">
                                  {tool.description || t("tools.noDescription")}
                                </p>
                              </div>
                              <Checkbox
                                checked={tool.enabled}
                                onChange={() => handleToolToggle(tool)}
                                disabled={!catalogAllowsSelection || tool.system_disabled}
                              onClick={(event) => event.stopPropagation()} />
                            </div>

                            {/* Parameters - Conditional Render */}
                            {isToolExpanded && hasParams && (
                              <div className="animate-[fade-in_150ms_ease-out]">
                                <div className="mx-2 sm:mx-4 mb-1.5 sm:mb-2 rounded-lg border border-[var(--theme-border)] overflow-hidden">
                                  {/* Table Header */}
                                  <div className="flex items-center gap-1.5 px-2.5 sm:px-3 py-1.5 bg-[var(--theme-bg-sidebar)] border-b border-[var(--theme-border)]">
                                    <Info
                                      size={10}
                                      className="text-[var(--theme-text-secondary)] sm:w-[11px] sm:h-[11px]"
                                    />
                                    <span className="text-xs sm:text-xs font-medium text-[var(--theme-text-secondary)] uppercase tracking-wide">
                                      {t("tools.parameters")}
                                    </span>
                                  </div>
                                  {/* Table Body */}
                                  <div className="bg-[var(--theme-workbench-panel)]">
                                    <Table
 size="small" pagination={false}
 dataSource={tool.parameters!}
 rowKey={(param) => param.name}
onRow={(_record) => {  return ({"className": `border-b border-[var(--theme-border)] last:border-b-0 hover:bg-[var(--theme-bg-sidebar)] transition-colors`}); }}
 columns={[
{ key: "name", title: t("tools.table.name"),
render: (_value, param) => (<>
<div className="flex items-center gap-1">
                                                  <code className="px-1.5 py-0.5 rounded bg-[var(--theme-bg-sidebar)] text-[var(--theme-text)] font-mono font-medium">
                                                    {param.name}
                                                  </code>
                                                  {param.required && (
                                                    <span className="text-[8px] px-1 py-0.5 rounded bg-[var(--theme-danger-soft)] text-[var(--theme-danger-text)] font-medium">
                                                      *
                                                    </span>
                                                  )}
                                                </div>
</>) },
{ key: "type", title: t("tools.table.type"),
render: (_value, param) => (<>
<span className="px-1.5 py-0.5 rounded bg-[var(--theme-bg-sidebar)] text-[var(--theme-text-secondary)] font-mono text-xs">
                                                  {param.type}
                                                </span>
</>) },
{ key: "description", title: t("tools.table.description"),
render: (_value, param) => (<>
{param.description || "-"}
</>) }
]}
/>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            );
          },
        )}
        {filteredTools.length === 0 && totalCount > 0 && (
          <div className="rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-4 py-6 text-center text-sm text-[var(--theme-text-secondary)]">
            {t("tools.noMatchingTools", "No matching tools")}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="px-4 sm:px-5 py-3 sm:py-3.5 border-t border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        <Button type="primary" block
          onClick={() => setIsOpen(false)}
        >
          {t("tools.done")}
        </Button>
      </div>
    </div>
  );

  return (
    <>
      {!externalOnOpenChange ? (
        <Button type="text" size="large" disabled={totalCount === 0}
          ref={triggerRef} onClick={() => setIsOpen(true)}
          aria-label={t("tools.title")} title={totalCount === 0 ? t("tools.noTools") : `${enabledCount}/${totalCount} ${t("tools.toolsEnabled")}`}
          icon={<Wrench size={18} />}
        />
      ) : null}
    <ModalRoot open={isOpen} onOpenChange={setIsOpen}>
      <ModalPortal>
        <ModalBackdrop data-composer-mcp-backdrop />
        <ModalPopup finalFocus={externalOnOpenChange ? undefined : triggerRef} width="min(40rem, 100vw)" className="!items-end sm:!items-center">{modalContent}</ModalPopup>
      </ModalPortal>
    </ModalRoot>
    </>
  );
}
