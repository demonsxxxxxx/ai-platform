import { useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, MessageSquare, MessageSquarePlus } from "lucide-react";
import toast from "react-hot-toast";
import { useTranslation } from "react-i18next";

import { ConfirmDialog } from "../../components/common/ConfirmDialog";
import { LoadingSpinner } from "../../components/common/LoadingSpinner";
import { SessionItem } from "../../components/sidebar/SessionItem";
import { groupSessionsByTime } from "../../components/panels/sessionHelpers";
import { sessionApi } from "../../services/api";
import type { BackendSession } from "../../services/api";
import type { SessionSidebarSessionSource } from "../../components/panels/SessionSidebar";

interface AgentConversationPanelProps {
  currentSessionId: string | null;
  onNewSession: () => void;
  onSelectSession: (sessionId: string) => void;
  source: SessionSidebarSessionSource;
}

/** Desktop-only conversation catalog for the active Agent workspace. */
export function AgentConversationPanel({
  currentSessionId,
  onNewSession,
  onSelectSession,
  source,
}: AgentConversationPanelProps) {
  const { t } = useTranslation();
  const [deleteSessionId, setDeleteSessionId] = useState<string | null>(null);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const groupedSessions = useMemo(
    () => groupSessionsByTime(source.sessions, t),
    [source.sessions, t],
  );

  const handleDelete = async () => {
    if (!deleteSessionId) return;
    try {
      await sessionApi.delete(deleteSessionId);
      source.removeSession(deleteSessionId);
      if (deleteSessionId === currentSessionId) onNewSession();
      toast.success(t("sidebar.sessionDeleted"));
    } catch (error) {
      console.error("Failed to delete Agent conversation:", error);
      toast.error(t("sidebar.deleteFailed"));
    } finally {
      setDeleteSessionId(null);
    }
  };

  const renderSession = (session: BackendSession) => (
    <div className="flex min-w-0 items-center gap-1" key={session.id}>
      <MessageSquare
        aria-hidden="true"
        className="ml-1.5 size-4 shrink-0 text-[var(--theme-primary)]"
        strokeWidth={1.8}
      />
      <div className="min-w-0 flex-1">
        <SessionItem
          session={session}
          isActive={currentSessionId === session.id}
          onSelect={() => onSelectSession(session.id)}
          onDelete={() => setDeleteSessionId(session.id)}
          onSessionUpdate={source.updateSession}
        />
      </div>
    </div>
  );

  return (
    <aside
      className={`relative hidden h-full shrink-0 flex-col border-r border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] transition-[width] duration-200 xl:flex ${isCollapsed ? "w-0 overflow-visible border-r-0" : "w-60"}`}
      data-agent-conversation-panel
    >
      {!isCollapsed ? (
        <>
          <header className="flex h-[62px] items-center px-5">
            <h2 className="text-lg font-semibold text-[var(--theme-text)]">对话</h2>
          </header>
          <div className="px-4 pb-3 pt-2">
            <button
              className="flex h-9 w-full items-center justify-center gap-1.5 rounded-full border border-[var(--theme-border)] text-sm font-medium text-[var(--theme-primary)] transition-colors hover:bg-[var(--theme-primary-light)]"
              onClick={onNewSession}
              type="button"
            >
              <MessageSquarePlus aria-hidden="true" size={15} />
              新建会话
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
            {source.isLoading ? (
              <div className="flex justify-center py-8">
                <LoadingSpinner size="sm" />
              </div>
            ) : groupedSessions.length ? (
              groupedSessions.map((group) => (
                <section className="mb-4" key={group.label}>
                  <h3 className="px-2 py-1 text-xs font-medium text-[var(--theme-text-tertiary)]">
                    {group.label}
                  </h3>
                  <div className="flex flex-col gap-1">{group.sessions.map(renderSession)}</div>
                </section>
              ))
            ) : (
              <p className="px-2 py-8 text-center text-sm text-[var(--theme-text-tertiary)]">
                还没有历史会话
              </p>
            )}

            {source.hasMore ? (
              <button
                className="flex w-full items-center justify-center py-2 text-xs text-[var(--theme-primary)] hover:underline disabled:opacity-50"
                disabled={source.isLoadingMore}
                onClick={() => void source.loadMore()}
                type="button"
              >
                {source.isLoadingMore ? <LoadingSpinner size="xs" /> : "加载更多"}
              </button>
            ) : null}
          </div>
        </>
      ) : null}
      <button
        aria-label={isCollapsed ? "展开历史会话" : "收起历史会话"}
        className="absolute -right-5 top-1/2 z-20 flex h-12 w-5 -translate-y-1/2 items-center justify-center rounded-r-lg bg-[var(--theme-text-secondary)] text-white shadow-sm transition-colors hover:bg-[var(--theme-text)]"
        onClick={() => setIsCollapsed((collapsed) => !collapsed)}
        title={isCollapsed ? "展开历史会话" : "收起历史会话"}
        type="button"
      >
        {isCollapsed ? <ChevronRight aria-hidden="true" size={16} /> : <ChevronLeft aria-hidden="true" size={16} />}
      </button>

      <ConfirmDialog
        cancelText={t("common.cancel")}
        confirmText={t("common.delete")}
        isOpen={deleteSessionId !== null}
        message={t("sidebar.deleteConfirm")}
        onCancel={() => setDeleteSessionId(null)}
        onConfirm={() => void handleDelete()}
        title={t("sidebar.deleteSession")}
        variant="danger"
      />
    </aside>
  );
}
