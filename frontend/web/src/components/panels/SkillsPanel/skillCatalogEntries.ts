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

export interface SkillCatalogPage {
  entries: SkillCatalogEntry[];
  total: number;
}

export interface SkillCatalogMetrics {
  total: number;
  enabled: number;
  visible: number;
}

export interface SkillCatalogSelection {
  selectedSkillId: string | null;
  changed: boolean;
}

export interface ArchivedSkillCatalogEntry {
  id: string;
  actionName: string;
  displayName: string;
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

/** Compute overview metrics before search, tag filters, or pagination. */
export function resolveSkillCatalogMetrics(
  entries: ReadonlyArray<SkillCatalogEntry>,
): SkillCatalogMetrics {
  return {
    total: entries.length,
    enabled: entries.filter((entry) => entry.runtimeEnabled === true).length,
    visible: entries.filter((entry) => entry.catalogStatus === "available")
      .length,
  };
}

/** Keep master-list selection and the detail panel on the same catalog entry. */
export function resolveSkillCatalogSelection(
  entries: ReadonlyArray<Pick<SkillCatalogEntry, "id">>,
  selectedSkillId: string | null,
): SkillCatalogSelection {
  if (
    selectedSkillId &&
    entries.some((entry) => entry.id === selectedSkillId)
  ) {
    return { selectedSkillId, changed: false };
  }
  const nextSkillId = entries[0]?.id ?? null;
  return {
    selectedSkillId: nextSkillId,
    changed: selectedSkillId !== nextSkillId,
  };
}

/** Bind runtime delete results back to the stable catalog ids used by master-detail UI. */
export function resolveArchivedSkillCatalogEntries(
  adminSkills: ReadonlyArray<AdminSkillCatalogItem>,
  actionNames: ReadonlyArray<string>,
): ArchivedSkillCatalogEntry[] {
  const resolved = new Map<string, ArchivedSkillCatalogEntry>();
  for (const actionName of actionNames) {
    const adminSkill = adminSkills.find(
      (item) => item.skillId === actionName || item.name === actionName,
    );
    const entry = {
      id: adminSkill?.skillId ?? actionName,
      actionName,
      displayName: adminSkill?.name ?? actionName,
    };
    resolved.set(entry.id, entry);
  }
  return [...resolved.values()];
}

export function removeArchivedActionSelections(
  selectedActionNames: ReadonlySet<string>,
  archivedActionNames: ReadonlyArray<string>,
): Set<string> {
  const next = new Set(selectedActionNames);
  archivedActionNames.forEach((actionName) => next.delete(actionName));
  return next;
}

/**
 * Paginate the complete authorized catalog locally, but never slice a page that
 * the server already paginated. This keeps the shared panel correct in both
 * catalog modes.
 */
export function resolveSkillCatalogPage({
  entries,
  page,
  pageSize,
  localPagination,
  serverTotal,
}: {
  entries: SkillCatalogEntry[];
  page: number;
  pageSize: number;
  localPagination: boolean;
  serverTotal: number;
}): SkillCatalogPage {
  if (!localPagination) {
    return { entries, total: serverTotal };
  }
  const start = (page - 1) * pageSize;
  return {
    entries: entries.slice(start, start + pageSize),
    total: entries.length,
  };
}
