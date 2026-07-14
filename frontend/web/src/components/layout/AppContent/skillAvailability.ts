import type { SkillResponse } from "../../../types";
import type { SettingsResponse } from "../../../types";

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

export function resolveSettingsBooleanProjection(
  settings: SettingsResponse | null,
  key: string,
): {
  known: boolean;
  value: boolean | undefined;
} {
  if (!settings) return { known: false, value: undefined };

  const item = Object.values(settings.settings)
    .flat()
    .find((setting) => setting.key === key);

  if (!item) return { known: false, value: undefined };

  return {
    known: true,
    value: item.value === true || item.value === "true",
  };
}

export function resolveComposerSkillsAvailability({
  isAuthenticated,
  catalogEffectivePermissions,
  catalogPermissionsKnown,
}: {
  isAuthenticated: boolean;
  canReadSkills: boolean;
  catalogEffectivePermissions: string[];
  catalogPermissionsKnown: boolean;
  enableSkillsSettingKnown: boolean;
  enableSkillsSetting: boolean;
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
