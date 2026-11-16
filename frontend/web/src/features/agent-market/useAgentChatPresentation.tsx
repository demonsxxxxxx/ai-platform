import { useMemo } from "react";
import type { ChatPresentation } from "../../components/chat/chatPresentation";
import type { AgentProfilePublicProjection } from "../../types/agentProfile";
import { AgentChatHeader, AgentChatOpening, AgentStarterPrompts } from "./AgentChatPresentation";

/** Agent-only UI stays with the feature; shared chat receives presentation slots. */
export function useAgentChatPresentation(profile?: AgentProfilePublicProjection): ChatPresentation | undefined {
  return useMemo(() => profile ? {
    header: <AgentChatHeader profile={profile} />,
    renderOpening: (composer) => <AgentChatOpening profile={profile} composer={composer} />,
    renderStarterPrompts: (disabled) => <AgentStarterPrompts profile={profile} disabled={disabled} />,
    disableSlashCommands: true,
  } : undefined, [profile]);
}
