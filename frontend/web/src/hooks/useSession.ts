/**
 * Session management hooks
 */

import { useState, useCallback, useEffect, useLayoutEffect, useMemo, useRef } from "react";
import { sessionApi, type BackendSession } from "../services/api";
import { useAuth } from "./useAuth";

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
  const { user } = useAuth();
  const authScopeKey = JSON.stringify([user?.tenant_id, user?.id]);
  const owner = useMemo(() => ({ authScopeKey, enabled }), [authScopeKey, enabled]);
  const ownerRef = useRef<typeof owner | null>(owner);
  ownerRef.current = owner;
  const requestRef = useRef(0);
  const activeRequestRef = useRef<number | null>(null);
  const [sessions, setSessions] = useState<BackendSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const loadMoreRef = useCallback((_element: HTMLElement | null) => {}, []);

  const fetchSessions = useCallback(async (soft = false) => {
    if (!owner.enabled || ownerRef.current !== owner) return;
    const request = ++requestRef.current;
    activeRequestRef.current = request;
    const isCurrent = () => ownerRef.current === owner && requestRef.current === request;
    if (!soft) setIsLoading(true);
    setError(null);
    try {
      const latest = await sessionApi.listAuthoritative();
      if (!isCurrent()) return;
      setSessions((previous) => reconcileSessionList({ previous, latest, removeMissing: !soft }));
    } catch (err) {
      if (isCurrent() && !soft) {
        setError(err instanceof Error ? err.message : "Failed to load sessions");
      }
    } finally {
      if (isCurrent()) {
        activeRequestRef.current = null;
        setIsLoading(false);
      }
    }
  }, [owner]);

  useLayoutEffect(() => {
    ownerRef.current = owner;
    requestRef.current += 1;
    activeRequestRef.current = null;
    setSessions([]);
    setIsLoading(false);
    setError(null);
    return () => {
      if (ownerRef.current === owner) ownerRef.current = null;
      requestRef.current += 1;
    };
  }, [owner]);

  useEffect(() => { void fetchSessions(); }, [fetchSessions]);
  const refresh = useCallback(() => fetchSessions(), [fetchSessions]);
  const softRefresh = useCallback(() => fetchSessions(true), [fetchSessions]);

  // A confirmed local mutation supersedes older reads, so a late list response
  // cannot resurrect a deleted row or replace its acknowledged title.
  const mutate = useCallback((apply: (previous: BackendSession[]) => BackendSession[]) => {
    if (ownerRef.current !== owner || !owner.enabled) return;
    const reload = activeRequestRef.current !== null;
    requestRef.current += 1;
    activeRequestRef.current = null;
    setIsLoading(false);
    setSessions(apply);
    if (reload) void fetchSessions(true);
  }, [fetchSessions, owner]);
  const prependSession = useCallback((session: BackendSession) => {
    mutate((previous) => previous.some((item) => item.id === session.id) ? previous : [session, ...previous]);
  }, [mutate]);
  const removeSession = useCallback((sessionId: string) => {
    mutate((previous) => previous.filter((session) => session.id !== sessionId));
  }, [mutate]);
  const updateSession = useCallback((session: BackendSession) => {
    mutate((previous) => previous.map((current) => current.id === session.id
      ? { ...current, ...session, agent_conversation: session.agent_conversation ?? current.agent_conversation }
      : current));
  }, [mutate]);

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
