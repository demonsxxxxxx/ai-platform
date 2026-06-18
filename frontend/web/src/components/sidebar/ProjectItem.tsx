import {
  useState,
  useEffect,
  useRef,
  forwardRef,
  useImperativeHandle,
} from "react";
import { useTranslation } from "react-i18next";
import type { BackendSession } from "../../services/api/session";
import type { Project } from "../../types";
import { useFilteredSessionList } from "../../hooks/useSession";
import { SessionItem } from "./SessionItem";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { DynamicIcon } from "../common/DynamicIcon";
import {
  formatUnreadCount,
  getUnreadCountForFavorites,
  getUnreadCountForProject,
  type UnreadBySession,
} from "./unreadCounts";

export interface ProjectItemHandle {
  refresh: () => Promise<void>;
  softRefresh: () => Promise<void>;
  prependSession: (session: BackendSession) => void;
  removeSession: (sessionId: string) => void;
  updateSession: (session: BackendSession) => void;
  sessions: BackendSession[];
}

interface ProjectItemProps {
  project: Project;
  currentSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  scrollRoot?: Element | null;
  unreadBySession?: UnreadBySession;
  favoritesOnly?: boolean;
}

export const ProjectItem = forwardRef<ProjectItemHandle, ProjectItemProps>(
  function ProjectItem(
    {
      project,
      currentSessionId,
      onSelectSession,
      unreadBySession = new Map(),
      scrollRoot,
      favoritesOnly = false,
    },
    ref,
  ) {
    const { t } = useTranslation();
    const [isExpanded, setIsExpanded] = useState(false);

    const isFavorites = project.type === "favorites";

    // ─── Per-project session list ──────────────────────────────────
    const listState = useFilteredSessionList(
      favoritesOnly ? { favoritesOnly: true } : { projectId: project.id },
      scrollRoot,
    );
    const {
      sessions,
      isLoading,
      isLoadingMore,
      hasMore,
      loadMoreRef,
      refresh,
      softRefresh,
      prependSession,
      removeSession,
      updateSession,
    } = listState;
    const unreadCount = favoritesOnly
      ? getUnreadCountForFavorites(sessions, unreadBySession)
      : getUnreadCountForProject({
          projectId: project.id,
          loadedSessions: sessions,
          unreadBySession,
        });

    // Only fetch when expanded (lazy loading)
    const hasLoadedRef = useRef(false);
    useEffect(() => {
      if (isExpanded && !hasLoadedRef.current) {
        hasLoadedRef.current = true;
        refresh();
      }
    }, [isExpanded, refresh]);

    // Expose handle to parent
    useImperativeHandle(
      ref,
      () => ({
        refresh,
        softRefresh,
        prependSession,
        removeSession,
        updateSession,
        sessions,
      }),
      [
        refresh,
        softRefresh,
        prependSession,
        removeSession,
        updateSession,
        sessions,
      ],
    );

    // Toggle expand/collapse
    const handleToggle = () => {
      setIsExpanded(!isExpanded);
    };

    return (
      <div>
        <div
          onClick={handleToggle}
          className={`group relative flex cursor-pointer items-center gap-3 h-10 rounded-[10px] px-[9px] transition-colors ${
            isExpanded
              ? "bg-stone-100/60 dark:bg-stone-800/40"
              : "hover:bg-stone-100 dark:hover:bg-stone-800/30"
          }`}
        >
          <DynamicIcon
            name={project.icon}
            size={20}
            className="shrink-0 text-stone-500 dark:text-stone-400 fill-current text-[20px]"
          />

          <div className="min-w-0 flex-1">
            <div className="truncate text-[13px] text-stone-600 dark:text-stone-400 group-hover:text-stone-700 dark:group-hover:text-stone-300 transition-colors">
              {isFavorites ? t("sidebar.favorites") : project.name}
            </div>
          </div>

          {unreadCount > 0 && (
            <span className="inline-flex h-4 min-w-[16px] flex-shrink-0 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-medium leading-none text-white">
              {formatUnreadCount(unreadCount)}
            </span>
          )}
        </div>

        {/* Expandable content - sessions list with independent pagination */}
        {isExpanded && (
          <div className="ml-3 mt-0.5 flex flex-col gap-px">
            {isLoading ? (
              <div className="flex justify-center py-4">
                <LoadingSpinner size="sm" color="text-[var(--theme-primary)]" />
              </div>
            ) : sessions.length > 0 ? (
              <>
                {sessions.map((session) => (
                  <SessionItem
                    key={session.id}
                    session={session}
                    isActive={session.id === currentSessionId}
                    onSelect={() => onSelectSession(session.id)}
                  />
                ))}
                {hasMore && (
                  <div ref={loadMoreRef} className="flex justify-center py-2">
                    {isLoadingMore && (
                      <LoadingSpinner
                        size="xs"
                        color="text-[var(--theme-primary)]"
                      />
                    )}
                  </div>
                )}
              </>
            ) : null}
          </div>
        )}
      </div>
    );
  },
);
