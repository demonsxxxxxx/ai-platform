import type { AdminSkillCatalogItem } from "../../../services/api/skill";
import type { SkillResponse } from "../../../types";

export type SkillCatalogStatus =
  | "available"
  | "hidden"
  | "disabled"
  | "unpublished";

export interface SkillCatalogEntry {
  id: string;
  displayName: string;
  description: string;
  runtimeSkill: SkillResponse | null;
  adminSkill: AdminSkillCatalogItem | null;
  actionName: string | null;
  version: string | null;
  fileCount: number | null;
  runtimeEnabled: boolean | null;
  catalogStatus: SkillCatalogStatus;
  tags: string[];
  updatedAt: string | null;
  publishedCatalogName: string | null;
}

function statusFor(
  adminSkill: AdminSkillCatalogItem | null,
  runtimeSkill: SkillResponse | null,
): SkillCatalogStatus {
  if (adminSkill) {
    if (!adminSkill.currentVersion) return "unpublished";
    if (adminSkill.distributionStatus !== "active") return "disabled";
    return adminSkill.visibleToUser ? "available" : "hidden";
  }
  if (!runtimeSkill?.is_published) return "unpublished";
  return runtimeSkill.marketplace_is_active ? "available" : "disabled";
}

function createEntry(
  runtimeSkill: SkillResponse | null,
  adminSkill: AdminSkillCatalogItem | null,
): SkillCatalogEntry {
  const id = adminSkill?.skillId ?? runtimeSkill?.name ?? "";
  return {
    id,
    displayName: adminSkill?.name ?? runtimeSkill?.name ?? id,
    description: adminSkill?.description ?? runtimeSkill?.description ?? "",
    runtimeSkill,
    adminSkill,
    actionName: runtimeSkill?.name ?? null,
    version:
      adminSkill?.latestVersion ??
      adminSkill?.currentVersion ??
      runtimeSkill?.expected_version ??
      null,
    fileCount: runtimeSkill?.file_count ?? null,
    runtimeEnabled: runtimeSkill?.enabled ?? null,
    catalogStatus: statusFor(adminSkill, runtimeSkill),
    tags: runtimeSkill?.tags ?? [],
    updatedAt: runtimeSkill?.updated_at ?? null,
    publishedCatalogName: runtimeSkill?.published_marketplace_name ?? null,
  };
}

/** Merge admin lifecycle records and the runtime-safe projection behind one stable Skill id. */
export function buildSkillCatalogEntries(
  runtimeSkills: SkillResponse[],
  adminSkills: AdminSkillCatalogItem[],
): SkillCatalogEntry[] {
  if (adminSkills.length === 0) {
    return runtimeSkills.map((skill) => createEntry(skill, null));
  }

  const runtimeByName = new Map(runtimeSkills.map((skill) => [skill.name, skill]));
  const matchedRuntimeNames = new Set<string>();
  const entries = adminSkills.map((adminSkill) => {
    const runtimeSkill =
      runtimeByName.get(adminSkill.skillId) ??
      runtimeByName.get(adminSkill.name) ??
      null;
    if (runtimeSkill) matchedRuntimeNames.add(runtimeSkill.name);
    return createEntry(runtimeSkill, adminSkill);
  });

  for (const runtimeSkill of runtimeSkills) {
    if (!matchedRuntimeNames.has(runtimeSkill.name)) {
      entries.push(createEntry(runtimeSkill, null));
    }
  }
  return entries;
}

export function filterSkillCatalogEntries(
  entries: SkillCatalogEntry[],
  query: string,
  selectedTags: string[],
): SkillCatalogEntry[] {
  const normalizedQuery = query.trim().normalize("NFKC").toLocaleLowerCase();
  return entries.filter((entry) => {
    if (
      normalizedQuery &&
      !`${entry.displayName}\n${entry.description}`
        .normalize("NFKC")
        .toLocaleLowerCase()
        .includes(normalizedQuery)
    ) {
      return false;
    }
    return selectedTags.every((tag) => entry.tags.includes(tag));
  });
}
