import { useCallback, useState } from "react";
import type { NavigateFunction } from "react-router-dom";
import { toast } from "@lobehub/ui/base-ui";
import { sessionApi } from "../../services/api/session";
import type { AgentConversationIdentity, AgentProfilePublicProjection } from "../../types/agentProfile";
import { buildAgentMarketDetailPath, buildAgentMarketWorkspacePath } from "./agentMarketSelection";

export type AgentConversationRecoveryPhase = "generic" | "loading" | "bound" | "blocked";

export interface AgentConversationRecoveryState {
  phase: AgentConversationRecoveryPhase;
  targetSessionId: string | null;
  identity: AgentConversationIdentity | null;
}

function conversationState(
  phase: AgentConversationRecoveryPhase,
  targetSessionId: string | null,
  identity: AgentConversationIdentity | null = null,
): AgentConversationRecoveryState {
  return { phase, targetSessionId, identity };
}

/** A bound or unresolved Session must not expose client capability controls. */
export function areAgentConversationControlsLocked(
  phase: AgentConversationRecoveryPhase,
): boolean {
  return phase !== "generic";
}

/** Remove a client capability control until the Session is proven generic. */
export function exposeGenericChatControl<T>(
  phase: AgentConversationRecoveryPhase,
  control: T,
): T | undefined {
  return areAgentConversationControlsLocked(phase) ? undefined : control;
}

interface AgentWorkspaceBindingInput {
  agentWorkspace?: Pick<AgentProfilePublicProjection, "agent_id">;
  state: AgentConversationRecoveryState;
  sessionId: string | null | undefined;
}

/** Accept Agent transcript data only after its Session and Agent identity agree. */
export function isExactAgentWorkspaceBinding({
  agentWorkspace,
  state,
  sessionId,
}: AgentWorkspaceBindingInput): boolean {
  if (!agentWorkspace) {
    return true;
  }

  return Boolean(
    sessionId &&
      state.phase === "bound" &&
      state.targetSessionId === sessionId &&
      state.identity?.agent_id === agentWorkspace.agent_id,
  );
}

/** Keep a prior Session transcript out of an Agent workspace until its binding is exact. */
export function projectAgentWorkspaceTranscript<T>({
  messages,
  ...binding
}: AgentWorkspaceBindingInput & { messages: T[] }): T[] {
  return isExactAgentWorkspaceBinding(binding) ? messages : [];
}

/** Generic Chat retains tool selection; an Agent workspace never receives it. */
export function getChatToolAccess({
  agentWorkspace,
  phase,
  sessionId,
}: {
  agentWorkspace?: Pick<AgentProfilePublicProjection, "agent_id">;
  phase: AgentConversationRecoveryPhase;
  sessionId: string | null;
}): { enabled: boolean; sessionId: string | null } {
  const locked = Boolean(agentWorkspace) || areAgentConversationControlsLocked(phase);
  return { enabled: !locked, sessionId: locked ? null : sessionId };
}

/** Recover and revalidate one server-owned Agent Conversation identity. */
export async function recoverAgentConversationIdentity(
  sessionId: string,
): Promise<AgentConversationIdentity | null> {
  const session = await sessionApi.getAuthoritative(sessionId);
  const identity = session.agent_conversation;
  if (session.session_id !== sessionId)
    throw new Error("agent_conversation_identity_mismatch");
  if (identity === null) return null;
  if (session.agent_id !== identity.agent_id)
    throw new Error("agent_conversation_identity_mismatch");
  return identity;
}


interface UseAgentConversationBindingOptions {
  agentWorkspace?: AgentProfilePublicProjection;
  routeSessionId?: string;
  navigate: NavigateFunction;
}

/** Session binding gates remain separate from the shared transcript/SSE owner. */
export function useAgentConversationBinding({
  agentWorkspace,
  routeSessionId,
  navigate,
}: UseAgentConversationBindingOptions) {
  const [state, setState] = useState(() => conversationState(
    agentWorkspace && routeSessionId ? "loading" : "generic",
    routeSessionId ?? null,
  ));
  const detailPath = agentWorkspace ? buildAgentMarketDetailPath(agentWorkspace) : "/agent-market";

  const resetForIdentity = useCallback(() => {
    setState(agentWorkspace && routeSessionId
      ? conversationState("loading", routeSessionId)
      : conversationState("generic", null));
  }, [agentWorkspace, routeSessionId]);
  const resetForNewTask = useCallback(() => setState(conversationState("generic", null)), []);
  const beginSelection = useCallback((sessionId: string) => {
    setState(conversationState("loading", sessionId));
  }, []);

  // The caller invokes this from its existing recovery effect so effect ordering is unchanged.
  const recover = useCallback((targetSessionId: string | null) => {
    if (!targetSessionId) {
      setState(conversationState("generic", null));
      return;
    }
    let active = true;
    setState(conversationState("loading", targetSessionId));
    void recoverAgentConversationIdentity(targetSessionId)
      .then((identity) => {
        if (!active) return;
        if (agentWorkspace && (!identity || identity.agent_id !== agentWorkspace.agent_id)) {
          throw new Error("agent_workspace_identity_mismatch");
        }
        setState(conversationState(identity ? "bound" : "generic", targetSessionId, identity));
        if (!agentWorkspace && identity) {
          navigate(buildAgentMarketWorkspacePath({ agent_id: identity.agent_id }, targetSessionId), { replace: true });
        }
      })
      .catch(() => {
        if (!active) return;
        setState(conversationState("blocked", targetSessionId));
        if (agentWorkspace) toast.error("该历史对话暂时无法打开，请重新选择或重试。");
        navigate(detailPath, { replace: true });
      });
    return () => { active = false; };
  }, [agentWorkspace, detailPath, navigate]);

  return { state, resetForIdentity, resetForNewTask, beginSelection, recover };
}
