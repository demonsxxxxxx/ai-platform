import { useRef, useEffect, useCallback, useLayoutEffect } from "react";
import { useNavigate, useParams, useLocation } from "react-router-dom";
import type { TabType } from "./types.ts";
import { shouldBlockSessionSelection } from "../../../utils/sessionSelectionGuard.ts";
import type { SessionConfig } from "../../../hooks/useAgent/types.ts";
import {
  shouldResetExternalNavigateFlag,
  type ExternalNavigationState,
} from "./externalNavigationState.ts";

interface UseSessionSyncOptions {
  activeTab: TabType;
  sessionId: string | null;
  loadHistory: (sessionId: string) => Promise<SessionConfig | null>;
  clearMessages: () => void;
  onConfigRestored?: (config: SessionConfig) => void;
  sessionRouteBasePath?: string;
  /** Defers URL-driven history loading until the caller authorizes the Session. */
  historyLoadEnabled?: boolean;
}

interface UseSessionSyncReturn {
  handleSelectSession: (selectedSessionId: string) => Promise<void>;
  handleNewSession: () => void;
}

interface SessionRouteSyncActionInput {
  activeTab: TabType;
  pathname: string;
  browserPathname?: string;
  sessionId: string | null;
  urlSessionId: string | undefined;
  externalNavigate: boolean;
  sessionRouteBasePath?: string;
}

interface SessionRouteSyncAction {
  type: "clear-external-state" | "replace-url";
  path: string;
}

interface ShouldLoadSessionFromUrlChangeInput {
  activeTab: TabType;
  sessionId: string | null;
  urlSessionId: string | undefined;
  isLoading: boolean;
  isNewSession: boolean;
  isInternalNavigation: boolean;
  initialUrlSyncPending?: boolean;
  historyLoadEnabled?: boolean;
}

interface ShouldClearConversationOnRouteIdentityChangeInput {
  hasAgentWorkspace: boolean;
  routeSessionId: string | undefined;
  sessionId: string | null;
}

interface UseConversationRouteIdentityResetOptions
  extends ShouldClearConversationOnRouteIdentityChangeInput {
  conversationIdentityKey: string;
  onIdentityChange: () => void;
}

interface SessionHistoryLoadOwner {
  requestId: number;
  sessionId: string;
}

export function isChatPath(pathname: string, sessionRouteBasePath = "/chat"): boolean {
  return (
    pathname === sessionRouteBasePath ||
    pathname.startsWith(`${sessionRouteBasePath}/`)
  );
}

export function getSessionRouteSyncAction({
  activeTab,
  pathname,
  browserPathname,
  sessionId,
  urlSessionId,
  externalNavigate,
  sessionRouteBasePath = "/chat",
}: SessionRouteSyncActionInput): SessionRouteSyncAction | null {
  const effectivePathname = browserPathname ?? pathname;

  if (activeTab !== "chat") {
    return externalNavigate
      ? { type: "clear-external-state", path: effectivePathname }
      : null;
  }

  if (externalNavigate) {
    return { type: "clear-external-state", path: effectivePathname };
  }

  // Guard against route transitions: if the current pathname is no longer a
  // chat route, never write a chat URL back into history from stale state.
  if (!isChatPath(effectivePathname, sessionRouteBasePath)) {
    return null;
  }

  if (sessionId && sessionId !== urlSessionId) {
    return { type: "replace-url", path: `${sessionRouteBasePath}/${sessionId}` };
  }

  if (!sessionId && urlSessionId) {
    return { type: "replace-url", path: sessionRouteBasePath };
  }

  return null;
}

export function getInitialUrlSyncCompletionAction({
  activeTab,
  pathname,
  browserPathname,
  externalNavigate,
  sessionRouteBasePath = "/chat",
}: Pick<
  SessionRouteSyncActionInput,
  | "activeTab"
  | "pathname"
  | "browserPathname"
  | "externalNavigate"
  | "sessionRouteBasePath"
>): SessionRouteSyncAction | null {
  const effectivePathname = browserPathname ?? pathname;

  if (activeTab !== "chat" || !externalNavigate) {
    return null;
  }

  if (!isChatPath(effectivePathname, sessionRouteBasePath)) {
    return null;
  }

  return { type: "clear-external-state", path: effectivePathname };
}

export function shouldLoadSessionFromUrlChange({
  activeTab,
  sessionId,
  urlSessionId,
  isLoading,
  isNewSession,
  isInternalNavigation,
  initialUrlSyncPending = false,
  historyLoadEnabled = true,
}: ShouldLoadSessionFromUrlChangeInput): boolean {
  if (!historyLoadEnabled) {
    return false;
  }

  if (activeTab !== "chat") {
    return false;
  }

  if (!urlSessionId) {
    return false;
  }

  if (isLoading || sessionId === urlSessionId) {
    return false;
  }

  if (initialUrlSyncPending) {
    return false;
  }

  if (isNewSession || isInternalNavigation) {
    return false;
  }

  return true;
}

/**
 * A first accepted submission may bind its Session before the URL is
 * canonicalized. That route-only transition must retain the live authority
 * and transcript in both ordinary and Agent Chat; every other identity
 * transition remains a real clear.
 */
export function shouldClearConversationOnRouteIdentityChange({
  hasAgentWorkspace,
  routeSessionId,
  sessionId,
}: ShouldClearConversationOnRouteIdentityChangeInput): boolean {
  if (hasAgentWorkspace && routeSessionId === sessionId) {
    return false;
  }

  return !routeSessionId || routeSessionId !== sessionId;
}

export function useConversationRouteIdentityReset({
  conversationIdentityKey,
  hasAgentWorkspace,
  routeSessionId,
  sessionId,
  onIdentityChange,
}: UseConversationRouteIdentityResetOptions): void {
  const previousIdentityKeyRef = useRef<string | undefined>(undefined);
  const onIdentityChangeRef = useRef(onIdentityChange);
  onIdentityChangeRef.current = onIdentityChange;

  useLayoutEffect(() => {
    if (previousIdentityKeyRef.current === conversationIdentityKey) {
      return;
    }
    previousIdentityKeyRef.current = conversationIdentityKey;
    if (
      !shouldClearConversationOnRouteIdentityChange({
        hasAgentWorkspace,
        routeSessionId,
        sessionId,
      })
    ) {
      return;
    }
    onIdentityChangeRef.current();
  }, [conversationIdentityKey, hasAgentWorkspace, routeSessionId, sessionId]);
}

export function useSessionSync({
  activeTab,
  sessionId,
  loadHistory,
  clearMessages,
  onConfigRestored,
  sessionRouteBasePath = "/chat",
  historyLoadEnabled = true,
}: UseSessionSyncOptions): UseSessionSyncReturn {
  const { sessionId: urlSessionId } = useParams<{ sessionId?: string }>();
  const location = useLocation();
  const navigate = useNavigate();

  // Session sync state - controlled by single ref to prevent sync loops
  const isSyncingRef = useRef(false);
  // Track if navigation was initiated internally (not from URL)
  const isInternalNavRef = useRef(false);
  const internalNavigationSourcePathRef = useRef<string | null>(null);
  // Track when a new session is being created to prevent loading stale history
  const isNewSessionRef = useRef(false);
  const initialUrlSyncPendingRef = useRef(false);
  const initialUrlSessionIdRef = useRef(urlSessionId);
  const initialUrlSyncStartedRef = useRef(false);
  const selectSessionRequestIdRef = useRef(0);
  const historyLoadRequestIdRef = useRef(0);
  const activeHistoryLoadRef = useRef<SessionHistoryLoadOwner | null>(null);
  // Track a single sync delay timeout for cleanup on unmount
  const syncTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Ref to store loadHistory to avoid stale closure in useEffect
  const loadHistoryRef = useRef(loadHistory);
  loadHistoryRef.current = loadHistory;

  // Ref to store onConfigRestored callback
  const onConfigRestoredRef = useRef(onConfigRestored);
  onConfigRestoredRef.current = onConfigRestored;

  // Use ref to store location pathname to avoid triggering on every render
  const locationPathRef = useRef(location.pathname);
  const locationStateRef = useRef(location.state);
  locationPathRef.current = location.pathname;
  locationStateRef.current = location.state;

  const beginHistoryLoad = useCallback(
    (targetSessionId: string): SessionHistoryLoadOwner => {
      const owner = {
        requestId: ++historyLoadRequestIdRef.current,
        sessionId: targetSessionId,
      };
      activeHistoryLoadRef.current = owner;
      return owner;
    },
    [],
  );

  const isCurrentHistoryLoad = useCallback(
    (owner: SessionHistoryLoadOwner): boolean =>
      activeHistoryLoadRef.current?.requestId === owner.requestId &&
      activeHistoryLoadRef.current.sessionId === owner.sessionId,
    [],
  );

  const finishHistoryLoad = useCallback(
    (owner: SessionHistoryLoadOwner) => {
      if (isCurrentHistoryLoad(owner)) {
        activeHistoryLoadRef.current = null;
      }
    },
    [isCurrentHistoryLoad],
  );

  const retireHistoryLoad = useCallback(() => {
    historyLoadRequestIdRef.current += 1;
    activeHistoryLoadRef.current = null;
  }, []);

  // Cleanup tracked timeouts on unmount
  useEffect(() => {
    return () => {
      if (syncTimeoutRef.current) {
        clearTimeout(syncTimeoutRef.current);
        syncTimeoutRef.current = null;
      }
      retireHistoryLoad();
    };
  }, [retireHistoryLoad]);

  const scheduleSyncReset = useCallback(() => {
    if (syncTimeoutRef.current) {
      clearTimeout(syncTimeoutRef.current);
    }
    syncTimeoutRef.current = setTimeout(() => {
      isSyncingRef.current = false;
      syncTimeoutRef.current = null;
    }, 100);
  }, []);

  // The first URL history load may wait for a caller-owned authorization gate.
  useEffect(() => {
    const initialUrlSessionId = initialUrlSessionIdRef.current;
    if (
      activeTab !== "chat" ||
      !historyLoadEnabled ||
      !initialUrlSessionId ||
      urlSessionId !== initialUrlSessionId ||
      initialUrlSyncStartedRef.current ||
      isSyncingRef.current
    ) {
      return;
    }

    initialUrlSyncStartedRef.current = true;
    isSyncingRef.current = true;
    initialUrlSyncPendingRef.current = true;
    const owner = beginHistoryLoad(initialUrlSessionId);
    loadHistoryRef
      .current(initialUrlSessionId)
      .then((config) => {
        if (
          isCurrentHistoryLoad(owner) &&
          config &&
          onConfigRestoredRef.current
        ) {
          onConfigRestoredRef.current(config);
        }
      })
      .finally(() => {
        if (!isCurrentHistoryLoad(owner)) {
          return;
        }
        initialUrlSyncPendingRef.current = false;
        finishHistoryLoad(owner);
        const action = getInitialUrlSyncCompletionAction({
          activeTab,
          pathname: locationPathRef.current,
          browserPathname:
            typeof window !== "undefined"
              ? window.location.pathname
              : undefined,
          externalNavigate: shouldResetExternalNavigateFlag(
            locationStateRef.current as ExternalNavigationState | null,
          ),
          sessionRouteBasePath,
        });
        if (action?.type === "clear-external-state") {
          navigate(action.path, { replace: true, state: null });
        }
        scheduleSyncReset();
      });
  }, [
    activeTab,
    beginHistoryLoad,
    finishHistoryLoad,
    historyLoadEnabled,
    isCurrentHistoryLoad,
    navigate,
    scheduleSyncReset,
    sessionRouteBasePath,
    urlSessionId,
  ]);

  // Load session when URL changes (e.g., from toast click)
  useEffect(() => {
    if (activeTab !== "chat") {
      selectSessionRequestIdRef.current += 1;
      if (activeHistoryLoadRef.current) {
        retireHistoryLoad();
      }
      isInternalNavRef.current = false;
      internalNavigationSourcePathRef.current = null;
      isNewSessionRef.current = false;
      if (initialUrlSyncPendingRef.current) {
        isSyncingRef.current = false;
      }
      initialUrlSyncPendingRef.current = false;
      return;
    }

    if (!urlSessionId) {
      if (activeHistoryLoadRef.current && !isInternalNavRef.current) {
        retireHistoryLoad();
      }
      if (initialUrlSyncPendingRef.current) {
        isSyncingRef.current = false;
      }
      initialUrlSyncPendingRef.current = false;
      if (isNewSessionRef.current) isNewSessionRef.current = false;
      return;
    }

    const activeHistoryLoad = activeHistoryLoadRef.current;

    if (sessionId === urlSessionId) {
      if (
        activeHistoryLoad &&
        activeHistoryLoad.sessionId !== urlSessionId
      ) {
        retireHistoryLoad();
      }
      if (isNewSessionRef.current) isNewSessionRef.current = false;
      if (isInternalNavRef.current) {
        isInternalNavRef.current = false;
        internalNavigationSourcePathRef.current = null;
      }
      return;
    }

    if (activeHistoryLoad?.sessionId === urlSessionId) {
      return;
    }

    if (isNewSessionRef.current) {
      isNewSessionRef.current = false;
      return;
    }

    if (
      isInternalNavRef.current &&
      internalNavigationSourcePathRef.current === locationPathRef.current
    ) {
      return;
    }
    if (isInternalNavRef.current) {
      isInternalNavRef.current = false;
      internalNavigationSourcePathRef.current = null;
    }

    if (
      !shouldLoadSessionFromUrlChange({
        activeTab,
        sessionId,
        urlSessionId,
        isLoading: false,
        isNewSession: isNewSessionRef.current,
        isInternalNavigation: isInternalNavRef.current,
        initialUrlSyncPending:
          initialUrlSyncPendingRef.current &&
          initialUrlSessionIdRef.current === urlSessionId,
        historyLoadEnabled,
      })
    ) {
      return;
    }

    if (
      initialUrlSyncPendingRef.current &&
      initialUrlSessionIdRef.current !== urlSessionId
    ) {
      initialUrlSyncPendingRef.current = false;
      isSyncingRef.current = false;
    }

    const owner = beginHistoryLoad(urlSessionId);
    loadHistoryRef
      .current(urlSessionId)
      .then((config) => {
        if (
          isCurrentHistoryLoad(owner) &&
          config &&
          onConfigRestoredRef.current
        ) {
          onConfigRestoredRef.current(config);
        }
      })
      .finally(() => {
        finishHistoryLoad(owner);
      });
  }, [
    activeTab,
    beginHistoryLoad,
    finishHistoryLoad,
    historyLoadEnabled,
    isCurrentHistoryLoad,
    retireHistoryLoad,
    sessionId,
    urlSessionId,
  ]);

  // Sync URL with sessionId state (when sessionId changes from internal actions)
  useEffect(() => {
    if (isSyncingRef.current) return;

    // An Agent workspace has an authoritative route Session but must not erase
    // that deep link before its caller finishes the identity binding check.
    if (!historyLoadEnabled && urlSessionId) return;

    const action = getSessionRouteSyncAction({
      activeTab,
      pathname: locationPathRef.current,
      browserPathname:
        typeof window !== "undefined" ? window.location.pathname : undefined,
      sessionId,
      urlSessionId,
      externalNavigate: shouldResetExternalNavigateFlag(
        locationStateRef.current as ExternalNavigationState | null,
      ),
      sessionRouteBasePath,
    });

    if (!action) {
      return;
    }

    if (action.type === "clear-external-state") {
      // Clear the externalNavigate flag using router navigation so the UI
      // stays in sync with the browser history state.
      navigate(action.path, { replace: true, state: null });
      return;
    }

    if (action.type === "replace-url") {
      isSyncingRef.current = true;
      navigate(action.path, { replace: true });
      scheduleSyncReset();
    }
  }, [
    activeTab,
    sessionId,
    urlSessionId,
    navigate,
    scheduleSyncReset,
    sessionRouteBasePath,
    historyLoadEnabled,
  ]);

  // Handle session selection from sidebar
  const handleSelectSession = useCallback(
    async (selectedSessionId: string) => {
      const currentPathname =
        typeof window !== "undefined" ? window.location.pathname : "";

      if (shouldBlockSessionSelection(currentPathname)) {
        return;
      }

      const requestId = ++selectSessionRequestIdRef.current;
      if (
        initialUrlSyncPendingRef.current &&
        initialUrlSessionIdRef.current !== selectedSessionId
      ) {
        initialUrlSyncPendingRef.current = false;
        isSyncingRef.current = false;
      }
      const owner = beginHistoryLoad(selectedSessionId);
      isInternalNavRef.current = true;
      internalNavigationSourcePathRef.current = currentPathname;
      try {
        const config = await loadHistory(selectedSessionId);
        const latestPathname =
          typeof window !== "undefined" ? window.location.pathname : "";

        if (
          requestId !== selectSessionRequestIdRef.current ||
          !isCurrentHistoryLoad(owner) ||
          !isChatPath(latestPathname, sessionRouteBasePath)
        ) {
          return;
        }

        if (config && onConfigRestoredRef.current) {
          onConfigRestoredRef.current(config);
        }
        navigate(`${sessionRouteBasePath}/${selectedSessionId}`);
      } catch (err) {
        console.error("[handleSelectSession] Error:", err);
      } finally {
        finishHistoryLoad(owner);
      }
    },
    [
      beginHistoryLoad,
      finishHistoryLoad,
      isCurrentHistoryLoad,
      loadHistory,
      navigate,
      sessionRouteBasePath,
    ],
  );

  // Handle new session - clear messages and navigate to /chat immediately.
  // Must navigate directly here instead of relying on the URL sync effect,
  // because the sync effect can be blocked by isSyncingRef (e.g., within
  // 100ms of a previous navigation). If the URL is not updated and still
  // holds the old session ID, the URL-change loading effect will later see
  // sessionId (new) !== urlSessionId (old) and call loadHistory with the
  // OLD session ID — overwriting the new session's messages.
  const handleNewSession = useCallback(() => {
    selectSessionRequestIdRef.current += 1;
    retireHistoryLoad();
    initialUrlSyncPendingRef.current = false;
    isSyncingRef.current = false;
    isNewSessionRef.current = true;
    isInternalNavRef.current = false;
    internalNavigationSourcePathRef.current = null;
    clearMessages();
    navigate(sessionRouteBasePath, { replace: true });
  }, [clearMessages, navigate, retireHistoryLoad, sessionRouteBasePath]);

  return {
    handleSelectSession,
    handleNewSession,
  };
}
