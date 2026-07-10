export interface ZipSkillPreview {
  name: string;
  description: string;
  file_count: number;
  files: string[];
  already_exists: boolean;
}

export function canSelectZipSkill(
  skill: ZipSkillPreview,
  allowNewSkills: boolean,
) {
  return allowNewSkills ? !skill.already_exists : skill.already_exists;
}

export function selectableZipSkillNames(
  skills: ZipSkillPreview[],
  allowNewSkills: boolean,
) {
  return skills
    .filter((skill) => canSelectZipSkill(skill, allowNewSkills))
    .map((skill) => skill.name);
}

export function initialZipSkillSelection(
  skills: ZipSkillPreview[],
  allowNewSkills: boolean,
) {
  const selectable = selectableZipSkillNames(skills, allowNewSkills);
  return allowNewSkills ? selectable.slice(0, 1) : selectable;
}

export function coerceZipSkillSelection(
  names: string[],
  skills: ZipSkillPreview[],
  allowNewSkills: boolean,
) {
  const selectable = new Set(selectableZipSkillNames(skills, allowNewSkills));
  const filtered = names.filter((name) => selectable.has(name));
  return allowNewSkills ? filtered.slice(0, 1) : filtered;
}

export function toggleZipSkillSelection(
  selectedNames: string[],
  name: string,
  skills: ZipSkillPreview[],
  allowNewSkills: boolean,
) {
  const skill = skills.find((item) => item.name === name);
  if (!skill || !canSelectZipSkill(skill, allowNewSkills)) {
    return selectedNames;
  }
  if (allowNewSkills) {
    return selectedNames.includes(name) ? [] : [name];
  }
  return selectedNames.includes(name)
    ? selectedNames.filter((selectedName) => selectedName !== name)
    : [...selectedNames, name];
}
