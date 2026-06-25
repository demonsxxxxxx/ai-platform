import { useState, useEffect, useCallback, useMemo } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { Bot, X } from "lucide-react";
import { useSwipeToClose } from "../../hooks/useSwipeToClose";

interface AgentModeSelectorProps {
  agents: { id: string; name: string; description: string }[];
  currentAgent: string;
  onSelectAgent?: (id: string) => void;
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  searchSeed?: string;
}

export function AgentModeSelector({
  agents,
  currentAgent,
  onSelectAgent,
  isOpen: externalIsOpen,
  onOpenChange: externalOnOpenChange,
  searchSeed,
}: AgentModeSelectorProps) {
  const { t } = useTranslation();
  const [internalOpen, setInternalOpen] = useState(false);
  const open = externalIsOpen ?? internalOpen;
  const setOpen = externalOnOpenChange ?? setInternalOpen;
  const [searchQuery, setSearchQuery] = useState("");

  const current = agents.find((a) => a.id === currentAgent);
  const filteredAgents = useMemo(() => {
    const normalized = searchQuery.trim().toLowerCase();
    if (!normalized) return agents;
    return agents.filter(
      (agent) =>
        agent.id.toLowerCase().includes(normalized) ||
        agent.name.toLowerCase().includes(normalized) ||
        agent.description.toLowerCase().includes(normalized),
    );
  }, [agents, searchQuery]);
  const sheetRef = useSwipeToClose({ onClose: () => setOpen(false) });

  const handleClose = useCallback(() => setOpen(false), [setOpen]);

  // Prevent background scroll when modal is open, restore previous value on close
  useEffect(() => {
    if (!open) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [open]);

  useEffect(() => {
    if (!open || searchSeed === undefined) return;
    setSearchQuery(searchSeed);
  }, [open, searchSeed]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [open, setOpen]);

  if (agents.length <= 1 || !onSelectAgent) return null;

  // When controlled externally, only render the modal — no trigger button
  if (externalOnOpenChange) {
    return open
      ? createPortal(
          <>
            <div
              data-yields-sidebar
              className="fixed inset-0 z-[300] bg-[var(--theme-overlay)] animate-fade-in"
              onClick={handleClose}
            />
            <div
              className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
              onClick={handleClose}
            >
              <div
                ref={sheetRef as React.Ref<HTMLDivElement>}
                className="w-full min-h-[40vh] max-h-[85vh] max-h-[85dvh] flex flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:w-[40%] sm:min-w-[600px] sm:max-h-[80vh] sm:rounded-lg"
                style={{ background: "var(--theme-bg-card)" }}
                onClick={(e) => e.stopPropagation()}
              >
                {/* Header */}
                <div
                  className="flex items-center justify-between px-4 sm:px-5 py-3 sm:py-4 border-b relative"
                  style={{ borderColor: "var(--theme-border)" }}
                >
                  <div className="absolute left-1/2 -translate-x-1/2 top-2 w-10 h-1 rounded-full bg-[var(--theme-border)] sm:hidden" />
                  <div className="flex items-center gap-3 mt-2 sm:mt-0">
                    <div className="size-9 sm:size-10 rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)] flex items-center justify-center">
                      <Bot
                        size={16}
                        className="text-[var(--theme-text-secondary)] sm:w-[18px] sm:h-[18px]"
                      />
                    </div>
                    <div>
                      <h2 className="text-sm sm:text-base font-semibold text-[var(--theme-text)]">
                        {t("agent.selectMode", "选择模式")}
                      </h2>
                      <p className="text-xs text-[var(--theme-text-secondary)]">
                        {t("agent.selectModeDesc", "切换智能体模式")}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={handleClose}
                    className="p-2 rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] active:bg-[var(--theme-bg-sidebar)] transition-colors"
                  >
                    <X size={18} />
                  </button>
                </div>

                <div className="border-b border-[var(--theme-border)] bg-[var(--theme-bg)] px-4 py-3 sm:px-5">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder={t("agent.searchPlaceholder", "Search agents")}
                    className="h-10 w-full rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-3 text-sm text-[var(--theme-text)] outline-none transition-colors placeholder:text-[var(--theme-text-secondary)] focus:border-[var(--theme-primary)] focus:ring-2 focus:ring-[var(--theme-primary-light)]"
                  />
                </div>

                {/* Agent list */}
                <div className="flex-1 overflow-y-auto py-2 sm:py-4 px-4 space-y-1.5">
                  {filteredAgents.map((agent) => {
                    const isActive = agent.id === currentAgent;
                    return (
                      <button
                        key={agent.id}
                        type="button"
                        className={`flex w-full items-center gap-3 px-3 sm:px-3.5 py-3 sm:py-3.5 rounded-lg text-left transition-all duration-200 ${
                          isActive
                            ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                            : "hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)]"
                        }`}
                        onClick={() => {
                          onSelectAgent(agent.id);
                          setOpen(false);
                        }}
                      >
                        <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg flex items-center justify-center shrink-0 bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)]">
                          <Bot
                            size={17}
                            className={`sm:w-[18px] sm:h-[18px] ${
                              isActive
                                ? "text-[var(--theme-primary)]"
                                : "text-[var(--theme-text-secondary)]"
                            }`}
                          />
                        </div>
                        <div className="flex-1 min-w-0">
                          <span
                            className={`text-[13px] sm:text-sm font-medium truncate block ${
                              isActive
                                ? "text-[var(--theme-text)]"
                                : "text-[var(--theme-text)]"
                            }`}
                          >
                            {t(agent.name)}
                          </span>
                          {agent.description && (
                            <p className="text-xs text-[var(--theme-text-secondary)] truncate mt-0.5 leading-relaxed text-left">
                              {t(agent.description)}
                            </p>
                          )}
                        </div>
                        {isActive && (
                          <div className="w-5 h-5 rounded-full bg-[var(--theme-primary)] flex items-center justify-center shrink-0">
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              width="12"
                              height="12"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="white"
                              strokeWidth="3"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M20 6 9 17l-5-5" />
                            </svg>
                          </div>
                        )}
                      </button>
                    );
                  })}
                  {filteredAgents.length === 0 && (
                    <div className="rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4 py-6 text-center text-sm text-[var(--theme-text-secondary)]">
                      {t("agent.noMatchingAgents", "No matching agents")}
                    </div>
                  )}
                </div>

                {/* Footer */}
                <div className="px-4 sm:px-5 py-3 sm:py-3.5 border-t border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                  <button
                    onClick={handleClose}
                    className="w-full py-2.5 px-4 bg-[var(--theme-primary)] text-white rounded-lg font-medium text-sm hover:bg-[var(--theme-primary-hover)] active:bg-[var(--theme-primary-hover)] transition-colors"
                  >
                    {t("common.done", "完成")}
                  </button>
                </div>
              </div>
            </div>
          </>,
          document.body,
        )
      : null;
  }

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="chat-tool-btn"
        title={current ? t(current.name) : ""}
      >
        <Bot size={18} />
      </button>

      {open &&
        createPortal(
          <>
            <div
              data-yields-sidebar
              className="fixed inset-0 z-[300] bg-[var(--theme-overlay)] animate-fade-in"
              onClick={handleClose}
            />

            <div
              className="fixed z-[301] sm:inset-0 sm:flex sm:items-center sm:justify-center sm:p-4 inset-x-0 bottom-0 animate-slide-up sm:animate-scale-in"
              onClick={handleClose}
            >
              <div
                ref={sheetRef as React.Ref<HTMLDivElement>}
                className="w-full min-h-[40vh] max-h-[85vh] max-h-[85dvh] flex flex-col overflow-hidden rounded-t-lg border border-[var(--theme-border)] shadow-[0_8px_24px_rgba(18,38,63,0.12)] sm:w-[40%] sm:min-w-[600px] sm:max-h-[80vh] sm:rounded-lg"
                style={{ background: "var(--theme-bg-card)" }}
                onClick={(e) => e.stopPropagation()}
              >
                {/* Header */}
                <div
                  className="flex items-center justify-between px-4 sm:px-5 py-3 sm:py-4 border-b relative"
                  style={{ borderColor: "var(--theme-border)" }}
                >
                  <div className="absolute left-1/2 -translate-x-1/2 top-2 w-10 h-1 rounded-full bg-[var(--theme-border)] sm:hidden" />
                  <div className="flex items-center gap-3 mt-2 sm:mt-0">
                    <div className="size-9 sm:size-10 rounded-lg bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)] flex items-center justify-center">
                      <Bot
                        size={16}
                        className="text-[var(--theme-text-secondary)] sm:w-[18px] sm:h-[18px]"
                      />
                    </div>
                    <div>
                      <h2 className="text-sm sm:text-base font-semibold text-[var(--theme-text)]">
                        {t("agent.selectMode", "选择模式")}
                      </h2>
                      <p className="text-xs text-[var(--theme-text-secondary)]">
                        {t("agent.selectModeDesc", "切换智能体模式")}
                      </p>
                    </div>
                  </div>
                  <button
                    onClick={handleClose}
                    className="p-2 rounded-lg text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] active:bg-[var(--theme-bg-sidebar)] transition-colors"
                  >
                    <X size={18} />
                  </button>
                </div>

                {/* Agent list */}
                <div className="flex-1 overflow-y-auto py-2 sm:py-4 px-4 space-y-1.5">
                  {agents.map((agent) => {
                    const isActive = agent.id === currentAgent;
                    return (
                      <button
                        key={agent.id}
                        type="button"
                        className={`flex w-full items-center gap-3 px-3 sm:px-3.5 py-3 sm:py-3.5 rounded-lg text-left transition-all duration-200 ${
                          isActive
                            ? "bg-[var(--theme-primary-light)] text-[var(--theme-text)]"
                            : "hover:bg-[var(--theme-bg-sidebar)] active:bg-[var(--theme-bg-sidebar)]"
                        }`}
                        onClick={() => {
                          onSelectAgent(agent.id);
                          setOpen(false);
                        }}
                      >
                        <div className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg flex items-center justify-center shrink-0 bg-[var(--theme-bg-sidebar)] ring-1 ring-[var(--theme-border)]">
                          <Bot
                            size={17}
                            className={`sm:w-[18px] sm:h-[18px] ${
                              isActive
                                ? "text-[var(--theme-primary)]"
                                : "text-[var(--theme-text-secondary)]"
                            }`}
                          />
                        </div>
                        <div className="flex-1 min-w-0">
                          <span
                            className={`text-[13px] sm:text-sm font-medium truncate block ${
                              isActive
                                ? "text-[var(--theme-text)]"
                                : "text-[var(--theme-text)]"
                            }`}
                          >
                            {t(agent.name)}
                          </span>
                          {agent.description && (
                            <p className="text-xs text-[var(--theme-text-secondary)] truncate mt-0.5 leading-relaxed text-left">
                              {t(agent.description)}
                            </p>
                          )}
                        </div>
                        {isActive && (
                          <div className="w-5 h-5 rounded-full bg-[var(--theme-primary)] flex items-center justify-center shrink-0">
                            <svg
                              xmlns="http://www.w3.org/2000/svg"
                              width="12"
                              height="12"
                              viewBox="0 0 24 24"
                              fill="none"
                              stroke="white"
                              strokeWidth="3"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            >
                              <path d="M20 6 9 17l-5-5" />
                            </svg>
                          </div>
                        )}
                      </button>
                    );
                  })}
                </div>

                {/* Footer */}
                <div className="px-4 sm:px-5 py-3 sm:py-3.5 border-t border-[var(--theme-border)] bg-[var(--theme-bg-sidebar)] pb-[max(0.75rem,env(safe-area-inset-bottom))]">
                  <button
                    onClick={handleClose}
                    className="w-full py-2.5 px-4 bg-[var(--theme-primary)] text-white rounded-lg font-medium text-sm hover:bg-[var(--theme-primary-hover)] active:bg-[var(--theme-primary-hover)] transition-colors"
                  >
                    {t("common.done", "完成")}
                  </button>
                </div>
              </div>
            </div>
          </>,
          document.body,
        )}
    </div>
  );
}
