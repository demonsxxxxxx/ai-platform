import { useMemo } from "react";
import type { ChatPresentation } from "../../components/chat/chatPresentation";
import type { AgentProfilePublicProjection } from "../../types/agentProfile";
import { AgentChatHeader, AgentChatOpening, AgentStarterPrompts } from "./AgentChatPresentation";

/** Agent-only UI stays with the feature; shared chat receives presentation slots. */
export function useAgentChatPresentation(profile?: AgentProfilePublicProjection): ChatPresentation | undefined {
  return useMemo(() => profile ? {
    identity: {
      agent_id: profile.agent_id,
      name: profile.name,
      avatar_ref: profile.avatar_ref,
      avatar_seed: profile.avatar_seed,
    },
    header: <AgentChatHeader profile={profile} />,
    renderOpening: (composer) => <AgentChatOpening profile={profile} composer={composer} />,
    renderStarterPrompts: (disabled) => <AgentStarterPrompts profile={profile} disabled={disabled} />,
    disableSlashCommands: true,
  } : undefined, [profile]);
}
