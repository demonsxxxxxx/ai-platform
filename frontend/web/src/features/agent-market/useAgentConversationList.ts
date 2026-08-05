import { useCallback, useEffect, useRef, useState } from "react";

import type { BackendSession } from "../../services/api";
import { agentProfileApi } from "../../services/api/agentProfile";
import type { AgentConversationSessionProjection } from "../../types/agentProfile";

const AGENT_CONVERSATION_PAGE_SIZE = 20;

function deduplicate(sessions: BackendSession[]): BackendSession[] {
  const seen = new Set<string>();
  return sessions.filter((session) => {
    if (seen.has(session.id)) return false;
    seen.add(session.id);
    return true;
  });
}

/** Convert the safe Agent conversation projection into the canonical sidebar shape. */
export function projectAgentConversationSidebarSession(
  session: AgentConversationSessionProjection,
): BackendSession {
  if (!session.created_at || !session.updated_at || !session.agent_conversation) {
    throw new Error("invalid_agent_conversation_catalog");
  }
  return {
    id: session.session_id,
    agent_id: session.agent_id,
    created_at: session.created_at,
    updated_at: session.updated_at,
    is_active: true,
    name: session.title || session.agent_conversation.name,
    metadata: {},
  };
}

export interface AgentConversationListController {
  sessions: BackendSession[];
  isLoading: boolean;
  isLoadingMore: boolean;
  hasMore: boolean;
  error: string | null;
  loadMore: () => Promise<void>;
  refresh: () => Promise<void>;
  softRefresh: () => Promise<void>;
  prependSession: (session: BackendSession) => void;
  removeSession: (sessionId: string) => void;
  updateSession: (session: BackendSession) => void;
}

/** Own cursor pagination for one immutable Agent/revision history. */
export function useAgentConversationList(
  agentId: string | undefined,
  revision: number | undefined,
): AgentConversationListController {
  const [sessions, setSessions] = useState<BackendSession[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const generationRef = useRef(0);
  const requestSequenceRef = useRef(0);
  const activeRequestRef = useRef<number | null>(null);
  const nextCursorRef = useRef<string | null>(null);

  const loadPage = useCallback(
    async (reset: boolean): Promise<void> => {
      if (!agentId || revision === undefined || revision < 1) return;
      if (activeRequestRef.current !== null) return;
      const requestId = ++requestSequenceRef.current;
      const generation = generationRef.current;
      activeRequestRef.current = requestId;
      if (reset) setIsLoading(true);
      else setIsLoadingMore(true);
      setError(null);
      try {
        const page = await agentProfileApi.listConversations(
          { agent_id: agentId, expected_revision: revision },
          {
            cursor: reset ? undefined : nextCursorRef.current ?? undefined,
            limit: AGENT_CONVERSATION_PAGE_SIZE,
          },
        );
        if (generation !== generationRef.current) return;
        const projected = page.sessions.map(projectAgentConversationSidebarSession);
        setSessions((current) =>
          deduplicate(reset ? projected : [...current, ...projected]),
        );
        nextCursorRef.current = page.next_cursor;
        setHasMore(page.next_cursor !== null);
      } catch (reason) {
        if (generation !== generationRef.current) return;
        setError(
          reason instanceof Error
            ? reason.message
            : "invalid_agent_conversation_catalog",
        );
        if (reset) setSessions([]);
        nextCursorRef.current = null;
        setHasMore(false);
      } finally {
        if (activeRequestRef.current === requestId) {
          activeRequestRef.current = null;
          setIsLoading(false);
          setIsLoadingMore(false);
        }
      }
    },
    [agentId, revision],
  );

  const refresh = useCallback(async () => {
    generationRef.current += 1;
    activeRequestRef.current = null;
    nextCursorRef.current = null;
    await loadPage(true);
  }, [loadPage]);

  const loadMore = useCallback(async () => {
    if (!nextCursorRef.current) return;
    await loadPage(false);
  }, [loadPage]);

  useEffect(() => {
    generationRef.current += 1;
    activeRequestRef.current = null;
    nextCursorRef.current = null;
    setSessions([]);
    setHasMore(false);
    setError(null);
    if (agentId && revision !== undefined && revision > 0) {
      void loadPage(true);
    }
    return () => {
      generationRef.current += 1;
      activeRequestRef.current = null;
    };
  }, [agentId, loadPage, revision]);

  const prependSession = useCallback((session: BackendSession) => {
    setSessions((current) => deduplicate([session, ...current]));
  }, []);
  const removeSession = useCallback((sessionId: string) => {
    setSessions((current) => current.filter((session) => session.id !== sessionId));
  }, []);
  const updateSession = useCallback((session: BackendSession) => {
    setSessions((current) =>
      current.map((item) => (item.id === session.id ? session : item)),
    );
  }, []);

  return {
    sessions,
    isLoading,
    isLoadingMore,
    hasMore,
    error,
    loadMore,
    refresh,
    softRefresh: refresh,
    prependSession,
    removeSession,
    updateSession,
  };
}
