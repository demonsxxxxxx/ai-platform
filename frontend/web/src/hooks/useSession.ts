/**
 * Session management hooks
 */

import { useState, useCallback, useEffect } from "react";
import { sessionApi, type BackendSession } from "../services/api";

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

// ─── Authorized active session list ────────────────────────────────

export interface UseSessionListReturn {
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

/** Lists the bounded, disclosure-safe active-session projection. */
export function useSessionList(
  _scrollRoot?: Element | null,
  enabled = true,
): UseSessionListReturn {
  const [sessions, setSessions] = useState<BackendSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadMoreRef = useCallback((_element: HTMLElement | null) => {}, []);

  const fetchSessions = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setSessions(dedup(await sessionApi.listAuthoritative()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load sessions");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    setSessions([]);
    if (enabled) void fetchSessions();
  }, [enabled, fetchSessions]);

  const refresh = useCallback(async () => {
    if (enabled) await fetchSessions();
  }, [enabled, fetchSessions]);

  const softRefresh = useCallback(async () => {
    if (!enabled) return;
    try {
      const newSessions = await sessionApi.listAuthoritative();
      setSessions((prev) =>
        reconcileSessionList({
          previous: prev,
          latest: newSessions,
          removeMissing: false,
        }),
      );
    } catch {
      // silent — soft refresh is best-effort
    }
  }, [enabled]);

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
    setSessions((prev) =>
      prev.map((current) =>
        current.id === session.id
          ? {
              ...current,
              ...session,
              agent_conversation:
                session.agent_conversation ?? current.agent_conversation,
            }
          : current,
      ),
    );
  }, []);

  return {
    sessions,
    isLoading,
    isLoadingMore: false,
    hasMore: false,
    error,
    loadMoreRef,
    refresh,
    softRefresh,
    prependSession,
    removeSession,
    updateSession,
  };
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
