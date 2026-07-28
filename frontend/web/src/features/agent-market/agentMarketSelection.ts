import type {
  AgentProfilePublicProjection,
  SelectedAgentProfileRequest,
} from "../../types";

/** Resolve a route selection only when the catalog still exposes that exact revision. */
export function selectPublishedMarketProfile(
  profiles: readonly AgentProfilePublicProjection[],
  agentId: string | undefined,
  revisionText: string | undefined,
): AgentProfilePublicProjection | null {
  const expectedRevision = Number(revisionText);
  if (
    !agentId?.trim() ||
    !Number.isSafeInteger(expectedRevision) ||
    expectedRevision < 1
  ) {
    return null;
  }
  return (
    profiles.find(
      (profile) =>
        profile.agent_id === agentId && profile.expected_revision === expectedRevision,
    ) ?? null
  );
}

/** Preserve the backend-owned immutable revision lock without local capabilities. */
export function marketProfileRequest(
  profile: AgentProfilePublicProjection,
): SelectedAgentProfileRequest {
  return {
    agent_id: profile.agent_id,
    expected_revision: profile.expected_revision,
  };
}

let pendingAgentMarketSelection: SelectedAgentProfileRequest | null = null;

/** Hold one server-owned market selection until the canonical Chat hook mounts. */
export function setPendingAgentMarketSelection(
  selection: SelectedAgentProfileRequest,
): void {
  pendingAgentMarketSelection = {
    agent_id: selection.agent_id,
    expected_revision: selection.expected_revision,
  };
}

/** Consume the pending selection once so a later generic Chat cannot inherit it. */
export function consumePendingAgentMarketSelection(): SelectedAgentProfileRequest | null {
  const selection = pendingAgentMarketSelection;
  pendingAgentMarketSelection = null;
  return selection;
}
