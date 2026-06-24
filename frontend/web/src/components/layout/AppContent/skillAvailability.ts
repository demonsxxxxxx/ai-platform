import type { SkillResponse } from "../../../types";
import type { SettingsResponse } from "../../../types";

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
  canReadSkills,
}: {
  canReadSkills: boolean;
  enableSkillsSettingKnown: boolean;
  enableSkillsSetting: boolean;
}): {
  shouldFetchSkills: boolean;
  enableComposerSkills: boolean;
} {
  const available = canReadSkills;

  return {
    shouldFetchSkills: available,
    enableComposerSkills: available,
  };
}
