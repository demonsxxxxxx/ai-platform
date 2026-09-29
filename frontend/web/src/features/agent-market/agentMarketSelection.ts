import type {
  AgentProfilePublicProjection,
  SelectedAgentProfileRequest,
} from "../../types";

/** Resolve a route selection only when the catalog still exposes that Agent. */
export function selectPublishedMarketProfile(
  profiles: readonly AgentProfilePublicProjection[],
  agentId: string | undefined,
): AgentProfilePublicProjection | null {
  if (!agentId?.trim()) return null;
  return (
    profiles.find((profile) => profile.agent_id === agentId) ?? null
  );
}

/** Build the shareable detail URL for one published Agent. */
export function buildAgentMarketDetailPath(
  profile: SelectedAgentProfileRequest,
): string {
  return `/agent-market/${encodeURIComponent(profile.agent_id)}`;
}

/** Build the dedicated workspace path for an Agent Conversation. */
export function buildAgentMarketWorkspacePath(
  profile: SelectedAgentProfileRequest,
  sessionId?: string,
): string {
  const base = `${buildAgentMarketDetailPath(profile)}/chat`;
  return sessionId ? `${base}/${encodeURIComponent(sessionId)}` : base;
}

export function marketTagsForProfile(
  profile: Pick<AgentProfilePublicProjection, "market_tags">,
): string[] {
  return [...new Set(profile.market_tags.map((tag) => tag.trim()).filter(Boolean))];
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
      ...marketTagsForProfile(profile),
      ...profile.starter_prompts,
    ].join("\n");
    return searchableProjection
      .normalize("NFKC")
      .toLocaleLowerCase()
      .includes(normalizedQuery);
  });
}

/** Filter by the selected tag union; empty selection leaves the catalog unchanged. */
export function filterPublishedMarketProfilesByTags(
  profiles: readonly AgentProfilePublicProjection[],
  selectedTags: readonly string[],
): readonly AgentProfilePublicProjection[] {
  const normalizedTags = [...new Set(selectedTags.map((tag) => tag.trim()).filter(Boolean))];
  if (normalizedTags.length === 0) return profiles;
  return profiles.filter((profile) => {
    const profileTags = marketTagsForProfile(profile);
    return normalizedTags.some((tag) => profileTags.includes(tag));
  });
}
