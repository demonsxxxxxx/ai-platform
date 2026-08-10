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

/** Build the shareable detail URL for one immutable published revision. */
export function buildAgentMarketDetailPath(
  profile: SelectedAgentProfileRequest,
): string {
  return `/agent-market/${encodeURIComponent(profile.agent_id)}/${profile.expected_revision}`;
}

/** Build the dedicated, revision-bound workspace path for an Agent Conversation. */
export function buildAgentMarketWorkspacePath(
  profile: SelectedAgentProfileRequest,
  sessionId?: string,
): string {
  const base = `${buildAgentMarketDetailPath(profile)}/chat`;
  return sessionId ? `${base}/${encodeURIComponent(sessionId)}` : base;
}

/** Search only the safe current public projection. */
export function filterPublishedMarketProfiles(
  profiles: readonly AgentProfilePublicProjection[],
  query: string,
): readonly AgentProfilePublicProjection[] {
  const normalizedQuery = query.trim().normalize("NFKC").toLocaleLowerCase();
  if (!normalizedQuery) return profiles;
  return profiles.filter((profile) => {
    const searchableProjection = [
      profile.name,
      profile.description,
      profile.capability_summary,
      ...profile.recommended_tasks,
    ].join("\n");
    return searchableProjection
      .normalize("NFKC")
      .toLocaleLowerCase()
      .includes(normalizedQuery);
  });
}
