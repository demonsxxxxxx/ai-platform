export type SkillsHubTab = "skills" | "marketplace";

export function resolveSkillsHubTab(
  requestedTab: SkillsHubTab | undefined,
  canReadSkills: boolean,
  canReadMarketplace: boolean,
): SkillsHubTab | null {
  if (requestedTab) {
    return requestedTab;
  }

  if (canReadSkills && canReadMarketplace) {
    return "skills";
  }

  if (canReadSkills) {
    return "skills";
  }

  if (canReadMarketplace) {
    return "marketplace";
  }

  return "marketplace";
}
