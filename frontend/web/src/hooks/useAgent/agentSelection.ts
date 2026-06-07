import type { AgentInfo } from "../../types";

export function resolveAvailableAgentId(
  currentAgentId: string,
  preferredDefaultAgentId: string | undefined,
  agents: AgentInfo[],
): string {
  const availableIds = new Set(agents.map((agent) => agent.id));

  if (currentAgentId && availableIds.has(currentAgentId)) {
    return currentAgentId;
  }

  if (preferredDefaultAgentId && availableIds.has(preferredDefaultAgentId)) {
    return preferredDefaultAgentId;
  }

  return agents[0]?.id || "";
}
