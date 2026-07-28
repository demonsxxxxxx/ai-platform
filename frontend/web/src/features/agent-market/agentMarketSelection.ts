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
