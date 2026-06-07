/**
 * Session management hooks
 */

import { useState, useCallback, useEffect, useRef } from "react";
import { useInView } from "react-intersection-observer";
import { sessionApi, type BackendSession } from "../services/api";

const PAGE_SIZE = 20;

function dedup(sessions: BackendSession[]): BackendSession[] {
  const seen = new Set<string>();
  return sessions.filter((s) => {
    if (seen.has(s.id)) return false;
    seen.add(s.id);
    return true;
  });
}

export function reconcileSessionList(input: {
  previous: BackendSession[];
  latest: BackendSession[];
  removeMissing: boolean;
}): BackendSession[] {
  const { previous, latest, removeMissing } = input;
  const latestIds = new Set(latest.map((session) => session.id));
  const merged = latest.map((session) => session);

  if (removeMissing) {
    return dedup(merged);
  }

  for (const session of previous) {
    if (!latestIds.has(session.id)) {
      merged.push(session);
    }
  }

  return dedup(merged);
}

// ─── Per-project paginated session list ─────────────────────────────

interface UseProjectSessionListReturn {
  sessions: BackendSession[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  error: string | null;
  loadMoreRef: React.RefCallback<HTMLElement>;
  refresh: () => Promise<void>;
  softRefresh: () => Promise<void>;
  prependSession: (session: BackendSession) => void;
  removeSession: (sessionId: string) => void;
  updateSession: (session: BackendSession) => void;
}

interface SessionListFilter {
  projectId?: string;
  favoritesOnly?: boolean;
}

export function useFilteredSessionList(
  filter: SessionListFilter,
  scrollRoot?: Element | null,
): UseProjectSessionListReturn {
  const [sessions, setSessions] = useState<BackendSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [skip, setSkip] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const loadedCountRef = useRef(PAGE_SIZE);

  const { ref: loadMoreRef, inView } = useInView({
    threshold: 0.1,
    root: scrollRoot ?? undefined,
  });

  const fetchSessions = async (reset = false) => {
    const targetSkip = reset ? 0 : skip;
    if (!reset && (isLoadingMore || !hasMore)) return;

    if (reset) {
      setIsLoading(true);
      setSkip(0);
    } else {
      setIsLoadingMore(true);
    }
    setError(null);

    try {
      const response = await sessionApi.list({
        project_id: filter.projectId,
        limit: PAGE_SIZE,
        skip: targetSkip,
        status: "active",
        favorites_only: filter.favoritesOnly,
      });

      const newSessions =
        "sessions" in response
          ? response.sessions
          : Array.isArray(response)
            ? response
            : [];
      const newHasMore = "has_more" in response ? response.has_more : false;

      if (reset) {
        setSessions(dedup(newSessions));
        setSkip(newSessions.length);
        loadedCountRef.current = Math.max(PAGE_SIZE, newSessions.length);
      } else {
        setSessions((prev) => dedup([...prev, ...newSessions]));
        setSkip(targetSkip + newSessions.length);
        loadedCountRef.current = Math.max(
          loadedCountRef.current,
          targetSkip + newSessions.length,
        );
      }
      setHasMore(newSessions.length > 0 ? newHasMore : false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sessions");
    } finally {
      setIsLoading(false);
      setIsLoadingMore(false);
    }
  };

  // Infinite scroll
  useEffect(() => {
    if (inView && hasMore && !isLoadingMore && !isLoading) {
      fetchSessions(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inView, hasMore, isLoadingMore, isLoading]);

  // Re-fetch when projectId changes
  useEffect(() => {
    setSessions([]);
    setSkip(0);
    setHasMore(false);
    loadedCountRef.current = PAGE_SIZE;
    fetchSessions(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.favoritesOnly, filter.projectId]);

  const refresh = useCallback(async () => {
    await fetchSessions(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter.favoritesOnly, filter.projectId]);

  const softRefresh = useCallback(async () => {
    try {
      const requestLimit = Math.min(
        100,
        Math.max(PAGE_SIZE, loadedCountRef.current),
      );
      const response = await sessionApi.list({
        project_id: filter.projectId,
        limit: requestLimit,
        skip: 0,
        status: "active",
        favorites_only: filter.favoritesOnly,
      });
      const newSessions =
        "sessions" in response
          ? response.sessions
          : Array.isArray(response)
            ? response
            : [];
      setSessions((prev) =>
        reconcileSessionList({
          previous: prev,
          latest: newSessions,
          removeMissing: filter.favoritesOnly || filter.projectId !== undefined,
        }),
      );
      loadedCountRef.current = Math.max(PAGE_SIZE, newSessions.length);
      setSkip(newSessions.length);
      setHasMore("has_more" in response ? response.has_more : false);
    } catch {
      // silent — soft refresh is best-effort
    }
  }, [filter.favoritesOnly, filter.projectId]);

  const prependSession = useCallback((session: BackendSession) => {
    setSessions((prev) => {
      if (prev.some((s) => s.id === session.id)) return prev;
      return [session, ...prev];
    });
  }, []);

  const removeSession = useCallback((sessionId: string) => {
    setSessions((prev) => prev.filter((s) => s.id !== sessionId));
  }, []);

  const updateSession = useCallback((session: BackendSession) => {
    setSessions((prev) => prev.map((s) => (s.id === session.id ? session : s)));
  }, []);

  return {
    sessions,
    isLoading,
    isLoadingMore,
    hasMore,
    error,
    loadMoreRef,
    refresh,
    softRefresh,
    prependSession,
    removeSession,
    updateSession,
  };
}

export function useProjectSessionList(
  projectId: string,
  scrollRoot?: Element | null,
): UseProjectSessionListReturn {
  return useFilteredSessionList({ projectId }, scrollRoot);
}

export function useFavoriteSessionList(
  scrollRoot?: Element | null,
): UseProjectSessionListReturn {
  return useFilteredSessionList({ favoritesOnly: true }, scrollRoot);
}

// ─── Single session operations ──────────────────────────────────────

interface UseSessionReturn {
  currentSession: BackendSession | null;
  isLoading: boolean;
  error: string | null;
  loadSession: (sessionId: string) => Promise<BackendSession | null>;
  deleteSession: (sessionId: string) => Promise<void>;
  switchSession: (sessionId: string | null) => void;
  clearError: () => void;
}

export function useSession(): UseSessionReturn {
  const [currentSession, setCurrentSession] = useState<BackendSession | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadSession = useCallback(
    async (sessionId: string): Promise<BackendSession | null> => {
      setIsLoading(true);
      setError(null);

      try {
        const session = await sessionApi.get(sessionId);
        if (session) {
          setCurrentSession(session);
        }
        return session;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load session");
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  const deleteSession = useCallback(
    async (sessionId: string) => {
      try {
        await sessionApi.delete(sessionId);
        if (currentSession?.id === sessionId) {
          setCurrentSession(null);
        }
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to delete session",
        );
      }
    },
    [currentSession],
  );

  const switchSession = useCallback(
    (sessionId: string | null) => {
      if (sessionId) {
        loadSession(sessionId);
      } else {
        setCurrentSession(null);
      }
    },
    [loadSession],
  );

  const clearError = useCallback(() => {
    setError(null);
  }, []);

  return {
    currentSession,
    isLoading,
    error,
    loadSession,
    deleteSession,
    switchSession,
    clearError,
  };
}

// ─── Message history loader ─────────────────────────────────────────

interface UseMessageHistoryReturn {
  loadHistory: (sessionId: string) => Promise<void>;
  isLoading: boolean;
  error: string | null;
}

export function useMessageHistory(
  onHistoryLoaded: (session: BackendSession) => void,
): UseMessageHistoryReturn {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadHistory = useCallback(
    async (sessionId: string) => {
      setIsLoading(true);
      setError(null);

      try {
        const session = await sessionApi.get(sessionId);
        if (session) {
          onHistoryLoaded(session);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load history");
      } finally {
        setIsLoading(false);
      }
    },
    [onHistoryLoaded],
  );

  return {
    loadHistory,
    isLoading,
    error,
  };
}
