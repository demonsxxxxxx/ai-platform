/**
 * Session sidebar component for displaying and managing chat history.
 */

import {
  useState,
  useEffect,
  useLayoutEffect,
  useRef,
  useCallback,
  useMemo,
  forwardRef,
  useImperativeHandle,
  type CSSProperties,
} from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "@lobehub/ui/base-ui";
import { useTranslation } from "react-i18next";
import { useInView } from "react-intersection-observer";
import type { ExternalStoreThreadListAdapter } from "@assistant-ui/react";
import { sessionApi, type BackendSession } from "../../services/api";
import { useAuth } from "../../hooks/useAuth";
import { useSessionList } from "../../hooks/useSession";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { RecentChatsDialog } from "../sidebar/RecentChatsDialog";
import {
  mergeUnreadUpdate,
  type UnreadBySession,
} from "../sidebar/unreadCounts";
import { SearchDialog } from "./SearchDialog";
import {
  SessionListContent,
  SidebarRail,
  createSessionThreadListAdapter,
} from "./SidebarParts";
import type { SessionActions } from "./SidebarParts";
import {
  getSafeWorkbenchNavPath,
  type WorkbenchNavItem,
} from "./SidebarParts/navigationState";
import { canAccessWorkbenchItem } from "../governance/workbenchAccessPolicy";
import type { AgentProfileAvatarRef } from "../../types/agentProfile";
import { LIBRECHAT_SHELL_GEOMETRY } from "../../librechat-ui/surface";

const NOOP_LOAD_MORE = async (): Promise<void> => undefined;

interface SessionSidebarProps {
  currentSessionId: string | null;
  onSelectSession: (sessionId: string) => void;
  onSelectGlobalHistorySession?: (session: BackendSession) => void;
  onNewSession: () => void;
  refreshKey?: number;
  newSession?: BackendSession | null;
  mobileOpen?: boolean;
  onMobileOpen?: () => void;
  onMobileClose?: () => void;
  isCollapsed?: boolean;
  onToggleCollapsed?: (collapsed: boolean) => void;
  sessionFilter?: (session: BackendSession) => boolean;
  sessionSource?: SessionSidebarSessionSource;
  globalHistorySource?: SessionSidebarSessionSource;
  agentWorkspace?: {
    agent_id?: string;
    avatar_ref?: AgentProfileAvatarRef;
    avatar_seed?: string;
    name: string;
    description: string;
  };
  /** Render Agent history in the adjacent workspace panel on desktop. */
  agentHistoryInMainPanel?: boolean;
  /** Navigation-only shells expose Agent services without generic Chat history. */
  navigationOnly?: boolean;
  /** Allow a navigation-only shell to show global history without chat actions. */
  showSessionHistory?: boolean;
  /** Called with the current identity-scoped session adapter. */
  onThreadListAdapterChange?: (
    adapter: ExternalStoreThreadListAdapter | undefined,
  ) => void;
}

export interface SessionSidebarSessionSource {
  sessions: BackendSession[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  loadMore: () => Promise<void>;
  softRefresh: () => Promise<void>;
  prependSession: (session: BackendSession) => void;
  removeSession: (sessionId: string) => void;
  updateSession: (session: BackendSession) => void;
}

export interface SessionSidebarHandle {
  updateSessionUnread: (
    sessionId: string,
    unreadCount: number,
  ) => void;
}

export const SessionSidebar = forwardRef<
  SessionSidebarHandle,
  SessionSidebarProps
>(function SessionSidebar(
  {
    currentSessionId,
    onSelectSession,
    onSelectGlobalHistorySession,
    onNewSession,
    newSession,
    mobileOpen = false,
    onMobileOpen,
    onMobileClose,
    isCollapsed: externalCollapsed,
    onToggleCollapsed,
    sessionFilter,
    sessionSource,
    globalHistorySource,
    agentWorkspace,
    agentHistoryInMainPanel = false,
    navigationOnly = false,
    showSessionHistory = false,
    onThreadListAdapterChange,
  },
  ref,
) {
  const { t } = useTranslation();
  const { user } = useAuth();
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const [isChatsCollapsed, setIsChatsCollapsed] = useState(false);
  const [scrollEl, setScrollEl] = useState<HTMLDivElement | null>(null);
  const [unreadBySession, setUnreadBySession] = useState<UnreadBySession>(
    () => new Map(),
  );
  const [isRecentChatsOpen, setIsRecentChatsOpen] = useState(false);

  const [isMobile, setIsMobile] = useState(
    () => window.matchMedia("(max-width: 639px)").matches,
  );

  const navigate = useNavigate();

  const navigateWorkbenchItem = useCallback(
    (item: WorkbenchNavItem) => {
      const destination = canAccessWorkbenchItem(user, item)
        ? getSafeWorkbenchNavPath(item, user)
        : "/agent-market";
      navigate(destination);
    },
    [navigate, user],
  );

  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  const isCollapsed = externalCollapsed ?? internalCollapsed;
  const setIsCollapsed = onToggleCollapsed ?? setInternalCollapsed;
  const sidebarGeometryStyle = {
    "--sidebar-rail-width": `${LIBRECHAT_SHELL_GEOMETRY.railWidthPx}px`,
    "--sidebar-width": `${LIBRECHAT_SHELL_GEOMETRY.expandedMinWidthPx}px`,
  } as CSSProperties;

  // ─── Hooks ──────────────────────────────────────────────────────

  const defaultSessionList = useSessionList(
    scrollEl,
    sessionSource === undefined &&
      (!navigationOnly || showSessionHistory),
  );
  const sessionList = sessionSource ?? defaultSessionList;
  const globalHistoryList = globalHistorySource ?? sessionList;
  const authScopeKey = JSON.stringify([user?.tenant_id ?? null, user?.id ?? null]);
  const ownerSnapshot = useMemo(
    () => ({
      authScopeKey,
      sessions: sessionList.sessions,
      updateSession: sessionList.updateSession,
      removeSession: sessionList.removeSession,
      prependSession: sessionList.prependSession,
      loadMore: sessionSource?.loadMore ?? NOOP_LOAD_MORE,
    }),
    [
      authScopeKey,
      sessionList.sessions,
      sessionList.updateSession,
      sessionList.removeSession,
      sessionList.prependSession,
      sessionSource?.loadMore,
    ],
  );
  const ownerSnapshotRef = useRef(ownerSnapshot);
  useLayoutEffect(() => {
    ownerSnapshotRef.current = ownerSnapshot;
  }, [ownerSnapshot]);
  const isOwnerSnapshotCurrent = useCallback(
    (snapshot: typeof ownerSnapshot) => {
      const current = ownerSnapshotRef.current;
      return (
        current.authScopeKey === snapshot.authScopeKey &&
        current.updateSession === snapshot.updateSession &&
        current.removeSession === snapshot.removeSession &&
        current.prependSession === snapshot.prependSession &&
        current.loadMore === snapshot.loadMore
      );
    },
    [],
  );
  const { ref: agentLoadMoreRef, inView: agentLoadMoreVisible } = useInView({
    threshold: 0.1,
    root: scrollEl ?? undefined,
  });
  useEffect(() => {
    if (
      sessionSource &&
      agentLoadMoreVisible &&
      sessionSource.hasMore &&
      !sessionSource.isLoading &&
      !sessionSource.isLoadingMore
    ) {
      void sessionSource.loadMore();
    }
  }, [agentLoadMoreVisible, sessionSource]);
  const visibleSessions = useMemo(
    () =>
      sessionSource
        ? sessionSource.sessions
        : sessionFilter
        ? sessionList.sessions.filter(sessionFilter)
        : sessionList.sessions,
    [sessionFilter, sessionList.sessions, sessionSource],
  );
  const loadMoreRef = sessionSource ? agentLoadMoreRef : defaultSessionList.loadMoreRef;

  const handleSessionUnread = useCallback(
    (sid: string, count: number) => {
      if (!isOwnerSnapshotCurrent(ownerSnapshot)) return;
      setUnreadBySession((prev) =>
        mergeUnreadUpdate(prev, {
          sessionId: sid,
          unreadCount: count,
        }),
      );
      const session = ownerSnapshot.sessions.find((s) => s.id === sid);
      if (session) {
        ownerSnapshot.updateSession({ ...session, unread_count: count });
      }
    },
    [isOwnerSnapshotCurrent, ownerSnapshot],
  );

  useImperativeHandle(
    ref,
    () => ({ updateSessionUnread: handleSessionUnread }),
    [handleSessionUnread],
  );

  const lastAppliedNewSessionKeyRef = useRef<string | null>(null);
  const desktopRecentChatsBtnRef = useRef<HTMLButtonElement>(null);
  const mobileRecentChatsBtnRef = useRef<HTMLButtonElement>(null);
  const [recentChatsAnchor, setRecentChatsAnchor] = useState<
    "desktop" | "mobile"
  >("desktop");

  // ─── Delete confirmation ────────────────────────────────────────

  const [deleteConfirm, setDeleteConfirm] = useState<{
    isOpen: boolean;
    sessionId: string | null;
    requestId: number;
  }>({ isOpen: false, sessionId: null, requestId: 0 });
  const deleteRequestIdRef = useRef(0);
  const currentSessionIdRef = useRef(currentSessionId);
  const onNewSessionRef = useRef(onNewSession);
  useLayoutEffect(() => {
    currentSessionIdRef.current = currentSessionId;
    onNewSessionRef.current = onNewSession;
  }, [currentSessionId, onNewSession]);

  const deleteOwnerRef = useRef<{
    snapshot: typeof ownerSnapshot;
  } | null>(null);
  useLayoutEffect(() => {
    deleteOwnerRef.current = { snapshot: ownerSnapshot };
    return () => {
      deleteOwnerRef.current = null;
    };
  }, [ownerSnapshot]);

  const confirmDeleteSession = async () => {
    const { sessionId, requestId } = deleteConfirm;
    const owner = deleteOwnerRef.current;
    if (!deleteConfirm.isOpen || !sessionId || !owner || !isOwnerSnapshotCurrent(owner.snapshot)) return;
    try {
      await sessionApi.delete(sessionId);
      if (!deleteOwnerRef.current || !isOwnerSnapshotCurrent(owner.snapshot)) {
        return;
      }
      owner.snapshot.removeSession(sessionId);
      if (currentSessionIdRef.current === sessionId) onNewSessionRef.current();
      if (deleteRequestIdRef.current === requestId) {
        toast.success(t("sidebar.sessionDeleted"));
        setDeleteConfirm({ isOpen: false, sessionId: null, requestId });
      }
    } catch (err) {
      if (!deleteOwnerRef.current || !isOwnerSnapshotCurrent(owner.snapshot)) {
        return;
      }
      if (deleteRequestIdRef.current === requestId) {
        console.error("Failed to delete session:", err);
        toast.error(t("sidebar.deleteFailed"));
        setDeleteConfirm({ isOpen: false, sessionId: null, requestId });
      }
    }
  };

  // ─── Effects ────────────────────────────────────────────────────

  useEffect(() => {
    // The dedicated Agent source already loads after server authorization and
    // refreshes explicitly after create/delete/update. Avoid a second first-page
    // request merely because the recovered route sets its current Session.
    if (!currentSessionId || sessionSource) return;
    sessionList.softRefresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSessionId]);

  useEffect(() => {
    if (!newSession || !newSession.id || !isOwnerSnapshotCurrent(ownerSnapshot)) {
      return;
    }
    const sessionKey = [
      newSession.id,
      newSession.updated_at,
      newSession.name ?? "",
    ].join(":");
    if (lastAppliedNewSessionKeyRef.current === sessionKey) return;
    ownerSnapshot.prependSession(newSession);
    ownerSnapshot.updateSession(newSession);
    lastAppliedNewSessionKeyRef.current = sessionKey;
  }, [isOwnerSnapshotCurrent, newSession, ownerSnapshot]);

  // ─── Keyboard shortcuts ──────────────────────────────────────────

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const isMac = navigator.platform.toUpperCase().indexOf("MAC") >= 0;
      const modifier = isMac ? e.metaKey : e.ctrlKey;
      if (!navigationOnly && modifier && e.key === "k") {
        e.preventDefault();
        setIsSearchOpen(true);
      }
      if (!navigationOnly && modifier && e.key === "n") {
        e.preventDefault();
        onNewSession();
      }
      if (
        !navigationOnly &&
        modifier &&
        e.shiftKey &&
        (e.key === "O" || e.key === "o")
      ) {
        e.preventDefault();
        onNewSession();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [navigationOnly, onNewSession]);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onMobileClose?.();
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [mobileOpen, onMobileClose]);

  // ─── Select session helper (mobile close) ───────────────────────

  const selectAndClose = useCallback(
    (sessionId: string) => {
      if (!isOwnerSnapshotCurrent(ownerSnapshot)) return;
      handleSessionUnread(sessionId, 0);
      onSelectSession(sessionId);
      onMobileClose?.();
    },
    [
      handleSessionUnread,
      isOwnerSnapshotCurrent,
      onMobileClose,
      onSelectSession,
      ownerSnapshot,
    ],
  );

  const requestDeleteSession = useCallback(
    (sessionId: string) => {
      if (!isOwnerSnapshotCurrent(ownerSnapshot)) return;
      const requestId = ++deleteRequestIdRef.current;
      setDeleteConfirm({ isOpen: true, sessionId, requestId });
    },
    [isOwnerSnapshotCurrent, ownerSnapshot],
  );

  const renameSession = useCallback(
    async (sessionId: string, title: string) => {
      if (!isOwnerSnapshotCurrent(ownerSnapshot)) return;
      const response = await sessionApi.update(sessionId, { name: title });
      if (!isOwnerSnapshotCurrent(ownerSnapshot)) return;
      ownerSnapshot.updateSession(response.session);
    },
    [isOwnerSnapshotCurrent, ownerSnapshot],
  );

  const selectNewSession = useCallback(() => {
    if (!isOwnerSnapshotCurrent(ownerSnapshot)) return;
    onNewSession();
  }, [isOwnerSnapshotCurrent, onNewSession, ownerSnapshot]);
  const updateGlobalHistorySession = globalHistoryList.updateSession;
  const selectGlobalHistorySession = useCallback(
    (session: BackendSession) => {
      if (ownerSnapshotRef.current.authScopeKey !== authScopeKey) return;
      setUnreadBySession((previous) =>
        mergeUnreadUpdate(previous, { sessionId: session.id, unreadCount: 0 }),
      );
      updateGlobalHistorySession({ ...session, unread_count: 0 });
      if (onSelectGlobalHistorySession) {
        onSelectGlobalHistorySession(session);
      } else {
        onSelectSession(session.id);
      }
      onMobileClose?.();
    },
    [
      authScopeKey,
      onMobileClose,
      onSelectGlobalHistorySession,
      onSelectSession,
      updateGlobalHistorySession,
    ],
  );

  const globalOwnerSnapshot = useMemo(
    () => ({
      authScopeKey,
      updateSession: globalHistoryList.updateSession,
    }),
    [authScopeKey, globalHistoryList.updateSession],
  );
  const globalOwnerSnapshotRef = useRef(globalOwnerSnapshot);
  useLayoutEffect(() => {
    globalOwnerSnapshotRef.current = globalOwnerSnapshot;
  }, [globalOwnerSnapshot]);
  const renameGlobalSession = useCallback(
    async (sessionId: string, title: string) => {
      const owner = globalOwnerSnapshotRef.current;
      if (owner.authScopeKey !== authScopeKey) return undefined;
      const response = await sessionApi.update(sessionId, { name: title });
      if (globalOwnerSnapshotRef.current !== owner) return undefined;
      owner.updateSession(response.session);
      return response.session;
    },
    [authScopeKey],
  );

  // ─── Aggregated action objects for SessionListContent ────────────

  const sessionActions: SessionActions = useMemo(
    () => ({
      onDeleteSession: requestDeleteSession,
      onSelectSession: selectAndClose,
    }),
    [requestDeleteSession, selectAndClose],
  );

  const adapterIdentity = JSON.stringify({
    scope: authScopeKey,
    workspace: agentWorkspace?.agent_id ?? null,
    currentSessionId,
    sessions: visibleSessions.map((session) => ({
      id: session.id,
      name: session.name ?? null,
      updatedAt: session.updated_at,
      unreadCount: session.unread_count ?? 0,
    })),
    isLoading: sessionList.isLoading,
    isLoadingMore: sessionList.isLoadingMore,
    hasMore: sessionList.hasMore,
    fallbackTitle: t("sidebar.newChat"),
  });
  const adapterIdentityRef = useRef(adapterIdentity);
  useLayoutEffect(() => {
    adapterIdentityRef.current = adapterIdentity;
  }, [adapterIdentity]);
  const adapterActionRef = useRef({
    onSelectSession: selectAndClose,
    onNewSession: selectNewSession,
    onRenameSession: renameSession,
    onDeleteSession: requestDeleteSession,
  });
  useLayoutEffect(() => {
    adapterActionRef.current = {
      onSelectSession: selectAndClose,
      onNewSession: selectNewSession,
      onRenameSession: renameSession,
      onDeleteSession: requestDeleteSession,
    };
  }, [
    renameSession,
    requestDeleteSession,
    selectAndClose,
    selectNewSession,
  ]);
  const threadListAdapter = useMemo(() => {
    const adapterOwnerIdentity = adapterIdentity;
    const actions = {
      onSelectSession: async (sessionId: string) => {
        if (adapterIdentityRef.current !== adapterOwnerIdentity) return;
        await adapterActionRef.current.onSelectSession(sessionId);
      },
      onNewSession: async () => {
        if (adapterIdentityRef.current !== adapterOwnerIdentity) return;
        adapterActionRef.current.onNewSession();
      },
      onRenameSession: async (sessionId: string, title: string) => {
        if (adapterIdentityRef.current !== adapterOwnerIdentity) return;
        await adapterActionRef.current.onRenameSession(sessionId, title);
      },
      onDeleteSession: async (sessionId: string) => {
        if (adapterIdentityRef.current !== adapterOwnerIdentity) return;
        adapterActionRef.current.onDeleteSession(sessionId);
      },
    };
    return createSessionThreadListAdapter({
      source: {
        sessions: visibleSessions,
        isLoading: sessionList.isLoading,
        isLoadingMore: sessionList.isLoadingMore,
        hasMore: sessionList.hasMore,
        loadMore: ownerSnapshot.loadMore,
      },
      currentSessionId,
      fallbackTitle: t("sidebar.newChat"),
      actions,
    }).adapter;
    // Semantic session identity controls replacement. Action callbacks are fenced
    // by the captured adapter identity and must not churn on parent rerenders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [adapterIdentity]);
  const adapterRegistrationRef = useRef<{
    callback: NonNullable<typeof onThreadListAdapterChange>;
    adapter: ExternalStoreThreadListAdapter | undefined;
  } | null>(null);
  useLayoutEffect(() => {
    if (!onThreadListAdapterChange) return;
    const registration = adapterRegistrationRef.current;
    if (registration?.callback !== onThreadListAdapterChange) {
      registration?.callback(undefined);
      adapterRegistrationRef.current = {
        callback: onThreadListAdapterChange,
        adapter: undefined,
      };
    }
    const current = adapterRegistrationRef.current;
    if (current && current.adapter !== threadListAdapter) {
      current.adapter = threadListAdapter;
      current.callback(threadListAdapter);
    }
  }, [onThreadListAdapterChange, threadListAdapter]);
  useLayoutEffect(
    () => () => {
      const registration = adapterRegistrationRef.current;
      if (registration && registration.callback === onThreadListAdapterChange) {
        registration.callback(undefined);
        adapterRegistrationRef.current = null;
      }
    },
    [onThreadListAdapterChange],
  );

  return (
    <>
      <div
        className={`fixed inset-0 z-[60] bg-[var(--theme-overlay-strong)] sm:hidden transition-opacity duration-300 ease-in-out ${
          mobileOpen ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
        style={{ height: "var(--app-viewport-height, 100dvh)" }}
        onClick={onMobileClose}
      />

      <div
        data-librechat-mobile-sidebar
        className={`fixed left-0 top-0 z-[70] flex w-64 flex-col rounded-r-lg border-r border-[var(--theme-border)] bg-[var(--theme-sidebar-panel)] transition-transform duration-300 ease-in-out sm:hidden ${
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        }`}
        style={{
          ...sidebarGeometryStyle,
          width: LIBRECHAT_SHELL_GEOMETRY.mobileMaxWidth,
          height: "var(--app-viewport-height, 100dvh)",
          paddingTop: "env(safe-area-inset-top)",
          paddingBottom: "env(safe-area-inset-bottom)",
        }}
      >
        {isMobile ? (
          <SessionListContent
            user={user}
            onCollapse={() => {
              setIsCollapsed(true);
              onMobileClose?.();
            }}
            onNewSession={onNewSession}
            onOpenSearch={() => setIsSearchOpen(true)}
            onSetScrollEl={setScrollEl}
            sessions={visibleSessions}
            globalSessions={globalHistoryList.sessions}
            globalIsLoading={globalHistoryList.isLoading}
            isLoading={sessionList.isLoading}
            hasMore={sessionList.hasMore}
            isLoadingMore={sessionList.isLoadingMore}
            loadMoreRef={loadMoreRef}
            onUpdateSession={sessionList.updateSession}
            onRenameSession={renameSession}
            onRenameGlobalSession={renameGlobalSession}
            currentSessionId={currentSessionId}
            unreadBySession={unreadBySession}
            sessionActions={sessionActions}
            onSelectGlobalSession={selectGlobalHistorySession}
            onUpdateGlobalSession={updateGlobalHistorySession}
            isChatsCollapsed={isChatsCollapsed}
            onToggleChatsCollapsed={() => setIsChatsCollapsed((v) => !v)}
            agentWorkspace={agentWorkspace}
            agentHistoryInMainPanel={agentHistoryInMainPanel}
            hideSessionDiscovery={agentWorkspace !== undefined}
            navigationOnly={navigationOnly}
            showSessionHistory={showSessionHistory}
          />
        ) : (
          <div className="flex-1" />
        )}
      </div>

      {/* Desktop: always render sidebar container */}
      <div
        data-librechat-desktop-sidebar
        className="hidden sm:flex h-full relative shrink-0 overflow-hidden bg-[var(--theme-sidebar-panel)]"
        style={{
          ...sidebarGeometryStyle,
          width: isCollapsed
            ? "var(--sidebar-rail-width)"
            : "var(--sidebar-width)",
        }}
      >
        {!isCollapsed ? (
          <div
            data-librechat-expanded-panel
            className="flex h-full w-full min-w-0 flex-col border-r border-[var(--theme-border)] bg-[var(--theme-sidebar-panel)]"
          >
            <SessionListContent
              user={user}
              onCollapse={() => setIsCollapsed(true)}
              onNewSession={onNewSession}
              onOpenSearch={() => setIsSearchOpen(true)}
              onSetScrollEl={setScrollEl}
              sessions={visibleSessions}
              globalSessions={globalHistoryList.sessions}
              globalIsLoading={globalHistoryList.isLoading}
              isLoading={sessionList.isLoading}
              hasMore={sessionList.hasMore}
              isLoadingMore={sessionList.isLoadingMore}
              loadMoreRef={loadMoreRef}
              onUpdateSession={sessionList.updateSession}
              onRenameSession={renameSession}
              onRenameGlobalSession={renameGlobalSession}
              currentSessionId={currentSessionId}
              unreadBySession={unreadBySession}
              sessionActions={sessionActions}
              onSelectGlobalSession={selectGlobalHistorySession}
              onUpdateGlobalSession={updateGlobalHistorySession}
              isChatsCollapsed={isChatsCollapsed}
              onToggleChatsCollapsed={() => setIsChatsCollapsed((v) => !v)}
              agentWorkspace={agentWorkspace}
              agentHistoryInMainPanel={agentHistoryInMainPanel}
              hideSessionDiscovery={agentWorkspace !== undefined}
              navigationOnly={navigationOnly}
              showSessionHistory={showSessionHistory}
            />
          </div>
        ) : (
          <div className="absolute inset-0">
            <SidebarRail
              user={user}
              isExpanded={false}
              onExpand={() => setIsCollapsed(false)}
              onCollapse={() => setIsCollapsed(true)}
              onNewSession={() => {
                onNewSession();
                setIsRecentChatsOpen(false);
              }}
              onOpenSearch={() => {
                setIsSearchOpen(true);
                setIsRecentChatsOpen(false);
              }}
              onOpenRecentChats={() => {
                setRecentChatsAnchor("desktop");
                setIsRecentChatsOpen(true);
              }}
              onOpenLaunchpad={() => navigate("/apps")}
              onOpenAgentMarket={() => navigate("/agent-market")}
              onOpenAgentBuilder={() => navigateWorkbenchItem("agentBuilder")}
              onOpenSkills={() => navigate("/skills")}
              onOpenPluginMarket={() => navigateWorkbenchItem("pluginMarket")}
              onOpenMcp={() => navigate("/mcp")}
              onOpenModels={() => navigateWorkbenchItem("models")}
              onOpenRuns={() => navigateWorkbenchItem("runs")}
              hideSessionDiscovery={agentWorkspace !== undefined}
              navigationOnly={navigationOnly}
              agentWorkspace={agentWorkspace !== undefined}
              recentChatsBtnRef={desktopRecentChatsBtnRef}
            />
          </div>
        )}
      </div>

      <div
        data-librechat-mobile-rail
        className="sm:hidden h-full relative shrink-0 overflow-hidden bg-[var(--theme-sidebar-rail)]"
        style={{
          ...sidebarGeometryStyle,
          width: "var(--sidebar-rail-width)",
        }}
      >
        <SidebarRail
          user={user}
          isExpanded={false}
          onExpand={() => onMobileOpen?.()}
          onCollapse={() => onMobileClose?.()}
          onNewSession={() => {
            onNewSession();
            setIsRecentChatsOpen(false);
          }}
          onOpenSearch={() => {
            setIsSearchOpen(true);
            setIsRecentChatsOpen(false);
          }}
          onOpenRecentChats={() => {
            setRecentChatsAnchor("mobile");
            setIsRecentChatsOpen(true);
          }}
          onOpenLaunchpad={() => navigate("/apps")}
          onOpenAgentMarket={() => navigate("/agent-market")}
          onOpenAgentBuilder={() => navigateWorkbenchItem("agentBuilder")}
          onOpenSkills={() => navigate("/skills")}
          onOpenPluginMarket={() => navigateWorkbenchItem("pluginMarket")}
          onOpenMcp={() => navigate("/mcp")}
          onOpenModels={() => navigateWorkbenchItem("models")}
          onOpenRuns={() => navigateWorkbenchItem("runs")}
          hideSessionDiscovery={agentWorkspace !== undefined}
          navigationOnly={navigationOnly}
          agentWorkspace={agentWorkspace !== undefined}
          recentChatsBtnRef={mobileRecentChatsBtnRef}
        />
      </div>

      {isSearchOpen && !agentWorkspace && !navigationOnly && (
        <SearchDialog
          isOpen={isSearchOpen}
          onClose={() => setIsSearchOpen(false)}
          onSelectSession={(sessionId) => {
            selectAndClose(sessionId);
            setIsSearchOpen(false);
          }}
        />
      )}

      <ConfirmDialog
        isOpen={deleteConfirm.isOpen}
        title={t("sidebar.deleteSession")}
        message={t("sidebar.deleteConfirm")}
        confirmText={t("common.delete")}
        cancelText={t("common.cancel")}
        onConfirm={confirmDeleteSession}
        onCancel={() => {
          const requestId = ++deleteRequestIdRef.current;
          setDeleteConfirm({
            isOpen: false,
            sessionId: null,
            requestId,
          });
        }}
        variant="danger"
      />

      {!agentWorkspace && !navigationOnly ? <RecentChatsDialog
        isOpen={isRecentChatsOpen}
        onClose={() => setIsRecentChatsOpen(false)}
        onSelectSession={(id) => selectAndClose(id)}
        currentSessionId={currentSessionId}
        anchorEl={
          recentChatsAnchor === "mobile"
            ? mobileRecentChatsBtnRef.current
            : desktopRecentChatsBtnRef.current
        }
      /> : null}
    </>
  );
});
