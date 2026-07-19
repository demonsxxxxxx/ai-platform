export interface ZipSkillPreview {
  name: string;
  description: string;
  file_count: number;
  files: string[];
  already_exists: boolean;
}

export type AdminReleaseAction = "review" | "promote" | "refresh" | "blocked";

export function adminReleaseActionForStatus(status: string): AdminReleaseAction {
  if (status === "draft") return "review";
  if (status === "reviewed") return "promote";
  if (status === "released") return "refresh";
  return "blocked";
}

export function canSelectZipSkill(
  skill: ZipSkillPreview,
  adminRelease: boolean,
) {
  // AI admins release exactly one package version and may target either a new
  // catalog Skill or a new immutable version of an existing catalog Skill.
  // Ordinary ZIP import remains an overlay and cannot create catalog Skills.
  return adminRelease || skill.already_exists;
}

export function selectableZipSkillNames(
  skills: ZipSkillPreview[],
  adminRelease: boolean,
) {
  return skills
    .filter((skill) => canSelectZipSkill(skill, adminRelease))
    .map((skill) => skill.name);
}

export function initialZipSkillSelection(
  skills: ZipSkillPreview[],
  adminRelease: boolean,
) {
  const selectable = selectableZipSkillNames(skills, adminRelease);
  return adminRelease ? selectable.slice(0, 1) : selectable;
}

export function coerceZipSkillSelection(
  names: string[],
  skills: ZipSkillPreview[],
  adminRelease: boolean,
) {
  const selectable = new Set(selectableZipSkillNames(skills, adminRelease));
  const filtered = names.filter((name) => selectable.has(name));
  return adminRelease ? filtered.slice(0, 1) : filtered;
}

export function toggleZipSkillSelection(
  selectedNames: string[],
  name: string,
  skills: ZipSkillPreview[],
  adminRelease: boolean,
) {
  const skill = skills.find((item) => item.name === name);
  if (!skill || !canSelectZipSkill(skill, adminRelease)) {
    return selectedNames;
  }
  if (adminRelease) {
    return selectedNames.includes(name) ? [] : [name];
  }
  return selectedNames.includes(name)
    ? selectedNames.filter((selectedName) => selectedName !== name)
    : [...selectedNames, name];
}
