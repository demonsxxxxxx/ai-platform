import type { SkillResponse } from "../../../types";

function hasSkillReadPermission(permissions: readonly string[]): boolean {
  return (
    permissions.includes("skill:read") || permissions.includes("skill:admin")
  );
}

export function buildEffectiveSkills({
  skills,
  skillsLoading,
  allowedSkillNames,
  disabledSkillNames,
}: {
  skills: SkillResponse[];
  skillsLoading: boolean;
  allowedSkillNames?: string[];
  disabledSkillNames?: string[];
}): SkillResponse[] {
  if (skillsLoading) return skills;

  const disabledSet = new Set(disabledSkillNames ?? []);
  const allowedSet =
    allowedSkillNames && allowedSkillNames.length > 0
      ? new Set(allowedSkillNames)
      : null;

  return skills
    .filter(
      (skill) => skill.enabled && (!allowedSet || allowedSet.has(skill.name)),
    )
    .map((skill) => ({
      ...skill,
      enabled: !disabledSet.has(skill.name),
    }));
}

export function countEnabledSkills(skills: SkillResponse[]): number {
  return skills.filter((skill) => skill.enabled).length;
}

export function resolveComposerSkillsAvailability({
  isAuthenticated,
  catalogEffectivePermissions,
  catalogPermissionsKnown,
}: {
  isAuthenticated: boolean;
  catalogEffectivePermissions: string[];
  catalogPermissionsKnown: boolean;
}): {
  shouldFetchSkills: boolean;
  enableComposerSkills: boolean;
} {
  const shouldFetchSkills = isAuthenticated;
  const catalogCanReadSkills = hasSkillReadPermission(
    catalogEffectivePermissions,
  );
  const available = shouldFetchSkills
    ? catalogPermissionsKnown
      ? catalogCanReadSkills
      : true
    : false;

  return {
    shouldFetchSkills,
    enableComposerSkills: available,
  };
}
