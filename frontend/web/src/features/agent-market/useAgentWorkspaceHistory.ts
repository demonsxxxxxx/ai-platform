import { useMemo } from "react";
import { useSessionList } from "../../hooks/useSession";
import type { BackendSession } from "../../services/api";
import { useAgentConversationList } from "./useAgentConversationList";

const loadGlobalHistoryMore = async () => {};

/** Compose the two server projections with one workspace mutation path. */
export function useAgentWorkspaceHistory(agentId: string | undefined) {
  const scoped = useAgentConversationList(agentId);
  const global = useSessionList(undefined, Boolean(agentId));
  const { prependSession: prependScoped, removeSession: removeScoped, updateSession: updateScoped } = scoped;
  const { prependSession: prependGlobal, removeSession: removeGlobal, updateSession: updateGlobal } = global;
  const mutations = useMemo(() => ({
    prependSession(session: BackendSession) {
      prependScoped(session);
      prependGlobal(session);
    },
    removeSession(sessionId: string) {
      removeScoped(sessionId);
      removeGlobal(sessionId);
    },
    updateSession(session: BackendSession) {
      updateScoped(session);
      updateGlobal(session);
    },
  }), [prependScoped, removeScoped, updateScoped, prependGlobal, removeGlobal, updateGlobal]);
  return {
    conversationList: { ...scoped, ...mutations },
    globalHistoryList: { ...global, loadMore: loadGlobalHistoryMore, ...mutations },
  };
}
