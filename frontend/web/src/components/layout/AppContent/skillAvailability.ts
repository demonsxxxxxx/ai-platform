import type { AgentInfo, SkillResponse } from "../../../types";

export function buildEffectiveSkills({
  skills,
  skillsLoading,
  personaSkillNames,
  disabledSkillNames,
}: {
  skills: SkillResponse[];
  skillsLoading: boolean;
  personaSkillNames?: string[];
  disabledSkillNames?: string[];
}): SkillResponse[] {
  if (skillsLoading) return skills;

  const disabledSet = new Set(disabledSkillNames ?? []);
  const personaSet =
    personaSkillNames && personaSkillNames.length > 0
      ? new Set(personaSkillNames)
      : null;

  return skills
    .filter(
      (skill) => skill.enabled && (!personaSet || personaSet.has(skill.name)),
    )
    .map((skill) => ({
      ...skill,
      enabled: !disabledSet.has(skill.name),
    }));
}

export function countEnabledSkills(skills: SkillResponse[]): number {
  return skills.filter((skill) => skill.enabled).length;
}

export function buildSkillOptionsFromAgents(
  agents: readonly AgentInfo[],
  currentAgent?: string,
): SkillResponse[] {
  return agents
    .filter((agent) => agent.id !== "general-agent")
    .map((agent) => ({
      name: agent.id,
      description: agent.description || agent.name,
      tags: ["runtime capability"],
      enabled: currentAgent ? agent.id === currentAgent : true,
      source: "manual",
      content: "",
      files: {},
      file_count: 0,
      installed_from: "manual",
      is_published: false,
      marketplace_is_active: true,
    }));
}
