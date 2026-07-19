/**
 * Skill API - 技能管理 (Simplified Architecture)
 *
 * New architecture: skills are stored as individual files in MongoDB.
 * - /api/skills/ - list, get, delete user skills
 * - /api/skills/{name}/files/{path} - read/write individual files
 * - /api/skills/{name}/toggle - enable/disable
 * - /api/marketplace/ - browse and install from marketplace
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  UserSkillDetail,
  UserSkill,
  SkillFileResponse,
  SkillToggleResponse,
  SkillCreate,
  MarketplaceSkillResponse,
  PublishToMarketplaceRequest,
  SkillsResponse,
} from "../../types/skill";

const SKILLS_API = `${API_BASE}/api/skills`;

export type AdminSkillVersionStatus =
  | "draft"
  | "reviewed"
  | "released"
  | "disabled"
  | "deprecated"
  | "active";

/** Safe lifecycle projection. Package, storage, and source metadata stay private. */
export interface AdminSkillVersionSummary {
  skillId: string;
  version: string;
  status: AdminSkillVersionStatus;
}

export interface AdminSkillUploadResponse {
  uploaded: AdminSkillVersionSummary;
}

/** Safe release-policy projection used only to verify the requested stable promotion. */
export interface AdminSkillReleasePolicy {
  skillId: string;
  channel: "stable";
  currentVersion: string;
  rolloutPercent: number;
  status: "active";
}

/** Safe admin catalog record. It intentionally excludes source, storage, and package data. */
export interface AdminSkillCatalogItem {
  skillId: string;
  name: string;
  description: string;
  lifecycleStatus: "active" | "disabled";
  distributionStatus: "active" | "disabled";
  visibleToUser: boolean;
  latestVersion: string | null;
  latestVersionStatus: AdminSkillVersionStatus | null;
  currentVersion: string | null;
  rolloutPercent: number | null;
}

/** ZIP preview projection; file paths remain in the package boundary. */
export interface SkillZipPreviewResponse {
  skill_count: number;
  skills: Array<{
    name: string;
    description: string;
    file_count: number;
    files: string[];
    already_exists: boolean;
  }>;
}

export interface SkillListParams {
  skip?: number;
  limit?: number;
  q?: string;
  tags?: string[];
}

export function buildSkillListUrl(params: SkillListParams = {}): string {
  const searchParams = new URLSearchParams();
  if (params.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params.limit !== undefined)
    searchParams.set("limit", String(params.limit));
  if (params.q) searchParams.set("q", params.q);
  params.tags?.forEach((tag) => searchParams.append("tags", tag));
  const query = searchParams.toString();
  return `${SKILLS_API}/${query ? `?${query}` : ""}`;
}

/**
 * Build the governed admin package-upload endpoint for a Skill version.
 */
export function buildAdminSkillUploadUrl(skillName: string): string {
  return `${API_BASE}/api/ai/admin/skills/${encodeURIComponent(
    skillName,
  )}/versions/upload`;
}

/**
 * Build the governed admin ZIP preview endpoint with global catalog existence.
 */
export function buildAdminSkillPreviewUrl(): string {
  return `${API_BASE}/api/ai/admin/skills/upload/preview`;
}

export function buildAdminSkillVersionStatusUrl(
  skillName: string,
  version: string,
): string {
  return `${API_BASE}/api/ai/admin/skills/${encodeURIComponent(
    skillName,
  )}/versions/${encodeURIComponent(version)}/status`;
}

export function buildAdminSkillPromoteUrl(skillName: string): string {
  return `${API_BASE}/api/ai/admin/skills/${encodeURIComponent(
    skillName,
  )}/promote`;
}

export function buildAdminSkillCatalogUrl(): string {
  return `${API_BASE}/api/ai/admin/skills`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isInteger(value) && (value as number) >= 0;
}

const ADMIN_SKILL_VERSION_STATUSES = new Set<AdminSkillVersionStatus>([
  "draft",
  "reviewed",
  "released",
  "disabled",
  "deprecated",
  "active",
]);

function isAdminSkillVersionStatus(
  value: unknown,
): value is AdminSkillVersionStatus {
  return (
    typeof value === "string" &&
    ADMIN_SKILL_VERSION_STATUSES.has(value as AdminSkillVersionStatus)
  );
}

function invalidAdminSkillLifecycle(): never {
  throw new Error("admin_skill_lifecycle_invalid");
}

/**
 * Drop the raw `source` tree and any package/storage fields before data reaches
 * application state. Lifecycle actions only need the immutable version identity
 * and its allowed lifecycle status.
 */
export function normalizeAdminSkillVersionResponse(
  response: unknown,
): AdminSkillVersionSummary {
  if (
    !isRecord(response) ||
    typeof response.skill_id !== "string" ||
    response.skill_id.trim().length === 0 ||
    typeof response.version !== "string" ||
    response.version.trim().length === 0 ||
    !isAdminSkillVersionStatus(response.status)
  ) {
    invalidAdminSkillLifecycle();
  }
  return {
    skillId: response.skill_id,
    version: response.version,
    status: response.status,
  };
}

export function normalizeAdminSkillUploadResponse(
  response: unknown,
): AdminSkillUploadResponse {
  if (!isRecord(response)) invalidAdminSkillLifecycle();
  return { uploaded: normalizeAdminSkillVersionResponse(response.uploaded) };
}

export function normalizeAdminSkillReleasePolicy(
  response: unknown,
): AdminSkillReleasePolicy {
  if (
    !isRecord(response) ||
    typeof response.skill_id !== "string" ||
    response.skill_id.trim().length === 0 ||
    response.channel !== "stable" ||
    typeof response.current_version !== "string" ||
    response.current_version.trim().length === 0 ||
    !isNonNegativeInteger(response.rollout_percent) ||
    response.rollout_percent !== 100 ||
    response.status !== "active"
  ) {
    invalidAdminSkillLifecycle();
  }
  return {
    skillId: response.skill_id,
    channel: "stable",
    currentVersion: response.current_version,
    rolloutPercent: response.rollout_percent,
    status: "active",
  };
}

function isNullableNonBlankString(value: unknown): value is string | null {
  return value === null || (typeof value === "string" && value.trim().length > 0);
}

export function normalizeAdminSkillCatalogResponse(
  response: unknown,
): AdminSkillCatalogItem[] {
  if (!isRecord(response) || !Array.isArray(response.items)) {
    invalidAdminSkillLifecycle();
  }
  return response.items.map((item) => {
    if (
      !isRecord(item) ||
      typeof item.skill_id !== "string" ||
      item.skill_id.trim().length === 0 ||
      typeof item.name !== "string" ||
      item.name.trim().length === 0 ||
      typeof item.description !== "string" ||
      (item.lifecycle_status !== "active" && item.lifecycle_status !== "disabled") ||
      (item.distribution_status !== "active" && item.distribution_status !== "disabled") ||
      typeof item.visible_to_user !== "boolean" ||
      !isNullableNonBlankString(item.latest_version) ||
      (item.latest_version_status !== null &&
        !isAdminSkillVersionStatus(item.latest_version_status)) ||
      !isNullableNonBlankString(item.current_version) ||
      (item.rollout_percent !== null &&
        (!isNonNegativeInteger(item.rollout_percent) || item.rollout_percent > 100)) ||
      (item.latest_version === null) !== (item.latest_version_status === null)
    ) {
      invalidAdminSkillLifecycle();
    }
    return {
      skillId: item.skill_id,
      name: item.name,
      description: item.description,
      lifecycleStatus: item.lifecycle_status,
      distributionStatus: item.distribution_status,
      visibleToUser: item.visible_to_user,
      latestVersion: item.latest_version,
      latestVersionStatus: item.latest_version_status,
      currentVersion: item.current_version,
      rolloutPercent: item.rollout_percent,
    };
  });
}

/** Discard raw package file paths after validating the ZIP selection preview. */
export function normalizeSkillZipPreviewResponse(
  response: unknown,
): SkillZipPreviewResponse {
  if (!isRecord(response) || !isNonNegativeInteger(response.skill_count)) {
    invalidAdminSkillLifecycle();
  }
  const rawSkills = response.skills;
  if (!Array.isArray(rawSkills) || rawSkills.length !== response.skill_count) {
    invalidAdminSkillLifecycle();
  }
  const skills = rawSkills.map((skill) => {
    if (
      !isRecord(skill) ||
      typeof skill.name !== "string" ||
      skill.name.trim().length === 0 ||
      typeof skill.description !== "string" ||
      !isNonNegativeInteger(skill.file_count) ||
      typeof skill.already_exists !== "boolean"
    ) {
      invalidAdminSkillLifecycle();
    }
    return {
      name: skill.name,
      description: skill.description,
      file_count: skill.file_count,
      // The current UI needs a count, not raw paths from the submitted ZIP.
      files: [],
      already_exists: skill.already_exists,
    };
  });
  return { skill_count: response.skill_count, skills };
}

function isUserSkill(value: unknown): value is UserSkill {
  if (!isRecord(value)) return false;
  return (
    typeof value.skill_name === "string" &&
    value.skill_name.trim().length > 0 &&
    typeof value.expected_version === "string" &&
    value.expected_version.trim().length > 0 &&
    isStringArray(value.input_modes) &&
    typeof value.requires_file === "boolean" &&
    typeof value.description === "string" &&
    isStringArray(value.tags) &&
    isStringArray(value.files) &&
    typeof value.enabled === "boolean" &&
    isNonNegativeInteger(value.file_count) &&
    (value.installed_from === "manual" || value.installed_from === "marketplace") &&
    typeof value.is_published === "boolean" &&
    typeof value.marketplace_is_active === "boolean"
  );
}

function invalidSkillCatalog(): never {
  throw new Error("authorized_skill_catalog_invalid");
}

export function normalizeSkillListResponse(response: unknown): SkillsResponse {
  if (Array.isArray(response)) {
    if (!response.every(isUserSkill)) invalidSkillCatalog();
    return {
      skills: response,
      total: response.length,
      skip: 0,
      limit: response.length,
      available_tags: [],
      effective_permissions: [],
      effective_permissions_known: false,
      catalog_read_resolved: true,
    };
  }

  if (!isRecord(response)) invalidSkillCatalog();
  const skills = response.skills;
  if (
    !Array.isArray(skills) ||
    !skills.every(isUserSkill) ||
    !isNonNegativeInteger(response.total) ||
    !isNonNegativeInteger(response.skip) ||
    !isNonNegativeInteger(response.limit) ||
    response.limit < 1 ||
    skills.length > response.limit ||
    response.skip + skills.length > response.total ||
    !isStringArray(response.available_tags) ||
    !isStringArray(response.effective_permissions) ||
    (response.catalog_read_resolved !== undefined &&
      typeof response.catalog_read_resolved !== "boolean")
  ) {
    invalidSkillCatalog();
  }
  const catalogReadResolved =
    typeof response.catalog_read_resolved === "boolean"
      ? response.catalog_read_resolved
      : true;

  return {
    skills,
    total: response.total,
    skip: response.skip,
    limit: response.limit,
    available_tags: response.available_tags,
    effective_permissions: response.effective_permissions,
    effective_permissions_known: true,
    catalog_read_resolved: catalogReadResolved,
  };
}

const AUTHORIZED_SKILL_PAGE_LIMIT = 200;
const AUTHORIZED_SKILL_MAX_PAGES = 1_000;

type SkillPageLoader = (params: SkillListParams) => Promise<SkillsResponse>;

/** Load the complete authorized public catalog without exposing partial pages. */
export async function collectAllAuthorizedSkills(
  listPage: SkillPageLoader,
): Promise<SkillsResponse> {
  const skillsByName = new Map<string, UserSkill>();
  const availableTags = new Set<string>();
  const effectivePermissions = new Set<string>();
  let effectivePermissionsKnown = true;
  let catalogReadResolved = true;
  let expectedTotal = 0;
  let skip = 0;
  let pageCount = 0;

  while (true) {
    pageCount += 1;
    if (pageCount > AUTHORIZED_SKILL_MAX_PAGES) {
      throw new Error("authorized_skill_catalog_page_limit");
    }
    const page = await listPage({
      skip,
      limit: AUTHORIZED_SKILL_PAGE_LIMIT,
    });
    if (page.skip !== skip) {
      throw new Error("authorized_skill_catalog_offset_mismatch");
    }
    const priorUniqueCount = skillsByName.size;
    expectedTotal = Math.max(expectedTotal, page.total);
    page.skills.forEach((skill) => skillsByName.set(skill.skill_name, skill));
    page.available_tags.forEach((tag) => availableTags.add(tag));
    page.effective_permissions.forEach((permission) =>
      effectivePermissions.add(permission),
    );
    effectivePermissionsKnown &&= page.effective_permissions_known;
    catalogReadResolved &&= page.catalog_read_resolved;

    if (page.skills.length === 0) {
      if (skillsByName.size >= expectedTotal) break;
      throw new Error("authorized_skill_catalog_incomplete");
    }
    if (skillsByName.size === priorUniqueCount) {
      throw new Error("authorized_skill_catalog_no_progress");
    }
    if (skillsByName.size >= expectedTotal) break;
    skip = skillsByName.size;
  }

  const skills = Array.from(skillsByName.values());
  return {
    skills,
    total: skills.length,
    skip: 0,
    limit: AUTHORIZED_SKILL_PAGE_LIMIT,
    available_tags: Array.from(availableTags),
    effective_permissions: Array.from(effectivePermissions),
    effective_permissions_known: effectivePermissionsKnown,
    catalog_read_resolved: catalogReadResolved,
  };
}

async function listSkills(params: SkillListParams = {}): Promise<SkillsResponse> {
  const response = await authFetch<unknown>(buildSkillListUrl(params));
  return normalizeSkillListResponse(response);
}

async function listAllAuthorizedSkills(): Promise<SkillsResponse> {
  return collectAllAuthorizedSkills(listSkills);
}

export const skillApi = {
  /** List the safe admin lifecycle catalog, including unpublished drafts. */
  async adminListSkills(): Promise<AdminSkillCatalogItem[]> {
    const response = await authFetch<unknown>(buildAdminSkillCatalogUrl());
    return normalizeAdminSkillCatalogResponse(response);
  },

  /**
   * List all user skills
   */
  async list(params: SkillListParams = {}): Promise<SkillsResponse> {
    return listSkills(params);
  },

  /** List every Skill in the current principal's public authorized catalog. */
  async listAllAuthorized(): Promise<SkillsResponse> {
    return listAllAuthorizedSkills();
  },

  /**
   * Get skill detail (with files list)
   */
  async get(skillName: string): Promise<UserSkillDetail> {
    return authFetch(`${SKILLS_API}/${encodeURIComponent(skillName)}`);
  },

  /**
   * Get skill file content
   */
  async getFile(
    skillName: string,
    filePath: string,
  ): Promise<SkillFileResponse> {
    return authFetch(
      `${SKILLS_API}/${encodeURIComponent(
        skillName,
      )}/files/${encodeURIComponent(filePath)}`,
    );
  },

  /**
   * Update skill file content
   */
  async updateFile(
    skillName: string,
    filePath: string,
    content: string,
  ): Promise<{ message: string }> {
    return authFetch(
      `${SKILLS_API}/${encodeURIComponent(
        skillName,
      )}/files/${encodeURIComponent(filePath)}`,
      {
        method: "PUT",
        body: JSON.stringify({ content }),
      },
    );
  },

  /**
   * Create skill - writes all files to /api/skills/{name}/files/{path}
   * Files are written sequentially; on failure, already-written files are rolled back.
   */
  async create(data: SkillCreate): Promise<{ message: string }> {
    // Build files dict from content (SKILL.md) or explicit files
    const filesToWrite: Record<string, string> = {};

    if (data.files && Object.keys(data.files).length > 0) {
      // Use explicit files from form
      Object.entries(data.files).forEach(([path, content]) => {
        filesToWrite[path] = content;
      });
    } else {
      // Fallback to content as SKILL.md
      filesToWrite["SKILL.md"] = data.content;
    }

    // Write files sequentially for atomicity
    const writtenPaths: string[] = [];
    try {
      for (const [filePath, content] of Object.entries(filesToWrite)) {
        await authFetch(
          `${SKILLS_API}/${encodeURIComponent(
            data.name,
          )}/files/${encodeURIComponent(filePath)}`,
          {
            method: "PUT",
            body: JSON.stringify({ content }),
          },
        );
        writtenPaths.push(filePath);
      }
    } catch (error) {
      // Rollback: delete already-written files
      await Promise.allSettled(
        writtenPaths.map((filePath) =>
          authFetch(
            `${SKILLS_API}/${encodeURIComponent(
              data.name,
            )}/files/${encodeURIComponent(filePath)}`,
            { method: "DELETE" },
          ),
        ),
      );
      throw error;
    }

    return { message: "Skill created" };
  },

  /**
   * Update skill metadata and content
   * Files are written/deleted sequentially to avoid partial failure leaving inconsistent state.
   */
  async update(
    skillName: string,
    data: {
      description?: string;
      content?: string;
      enabled?: boolean;
      files?: Record<string, string>;
      deletedFiles?: string[];
    },
  ): Promise<{ message: string }> {
    // Update SKILL.md if content changed (legacy single-file mode)
    if (data.content !== undefined && !data.files) {
      await authFetch(
        `${SKILLS_API}/${encodeURIComponent(skillName)}/files/SKILL.md`,
        {
          method: "PUT",
          body: JSON.stringify({ content: data.content }),
        },
      );
    }

    // Write new/updated files sequentially
    if (data.files) {
      for (const [filePath, content] of Object.entries(data.files)) {
        await authFetch(
          `${SKILLS_API}/${encodeURIComponent(
            skillName,
          )}/files/${encodeURIComponent(filePath)}`,
          {
            method: "PUT",
            body: JSON.stringify({ content }),
          },
        );
      }
    }

    // Delete removed files sequentially
    if (data.deletedFiles && data.deletedFiles.length > 0) {
      for (const filePath of data.deletedFiles) {
        await authFetch(
          `${SKILLS_API}/${encodeURIComponent(
            skillName,
          )}/files/${encodeURIComponent(filePath)}`,
          { method: "DELETE" },
        );
      }
    }

    // Toggle if enabled changed
    if (data.enabled !== undefined) {
      await this.toggle(skillName, data.enabled);
    }

    return { message: "Updated" };
  },

  /**
   * Delete (uninstall) user skill
   */
  async delete(skillName: string): Promise<{ message: string }> {
    return authFetch(`${SKILLS_API}/${encodeURIComponent(skillName)}`, {
      method: "DELETE",
    });
  },

  /**
   * Toggle skill enabled state
   */
  async toggle(
    skillName: string,
    enabled?: boolean,
  ): Promise<SkillToggleResponse> {
    const body = enabled !== undefined ? { enabled } : undefined;
    return authFetch(`${SKILLS_API}/${encodeURIComponent(skillName)}/toggle`, {
      method: "PATCH",
      body: body ? JSON.stringify(body) : undefined,
    });
  },

  /**
   * Preview skills in a ZIP file (without creating them)
   */
  async previewZip(file: File): Promise<SkillZipPreviewResponse> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await authFetch<unknown>(`${SKILLS_API}/upload/preview`, {
      method: "POST",
      body: formData,
    });
    return normalizeSkillZipPreviewResponse(response);
  },

  /**
   * Preview a ZIP file through the admin Skill path with global catalog checks.
   */
  async adminPreviewZip(file: File): Promise<SkillZipPreviewResponse> {
    const formData = new FormData();
    formData.append("file", file);
    const response = await authFetch<unknown>(buildAdminSkillPreviewUrl(), {
      method: "POST",
      body: formData,
    });
    return normalizeSkillZipPreviewResponse(response);
  },

  /**
   * Upload skill(s) from ZIP file (optionally filter by skill names)
   */
  async uploadZip(
    file: File,
    skillNames?: string[],
  ): Promise<{
    message: string;
    created: Array<{ name: string; file_count: number }>;
    errors: Array<{ name: string; reason: string }>;
    skill_count: number;
  }> {
    const formData = new FormData();
    formData.append("file", file);
    if (skillNames && skillNames.length > 0) {
      formData.append("skill_names", skillNames.join(","));
    }
    return authFetch(`${SKILLS_API}/upload`, {
      method: "POST",
      body: formData,
    });
  },

  /**
   * Upload a governed package version through the admin Skill release path.
   */
  async adminUploadZip(
    skillName: string,
    file: File,
  ): Promise<AdminSkillUploadResponse> {
    const formData = new FormData();
    formData.append("package", file);
    const response = await authFetch<unknown>(buildAdminSkillUploadUrl(skillName), {
      method: "POST",
      body: formData,
    });
    return normalizeAdminSkillUploadResponse(response);
  },

  /** Mark an immutable draft version as reviewed before stable promotion. */
  async adminReviewSkillVersion(
    skillName: string,
    version: string,
  ): Promise<AdminSkillVersionSummary> {
    const response = await authFetch<unknown>(
      buildAdminSkillVersionStatusUrl(skillName, version),
      {
        method: "POST",
        body: JSON.stringify({ status: "reviewed" }),
      },
    );
    return normalizeAdminSkillVersionResponse(response);
  },

  /** Promote a reviewed version to the fully rolled-out stable channel. */
  async adminPromoteSkillVersion(
    skillName: string,
    version: string,
  ): Promise<AdminSkillReleasePolicy> {
    const response = await authFetch<unknown>(
      buildAdminSkillPromoteUrl(skillName),
      {
        method: "POST",
        body: JSON.stringify({
          version,
          channel: "stable",
          rollout_percent: 100,
        }),
      },
    );
    return normalizeAdminSkillReleasePolicy(response);
  },

  /**
   * Preview skills from GitHub repository
   */
  async previewGitHub(
    repoUrl: string,
    branch: string = "main",
  ): Promise<{
    repo_url: string;
    branch: string;
    skills: Array<{ name: string; path: string; description: string }>;
  }> {
    return authFetch(`${API_BASE}/api/github/preview`, {
      method: "POST",
      body: JSON.stringify({ repo_url: repoUrl, branch }),
    });
  },

  /**
   * Install skills from GitHub repository
   */
  async installGitHub(
    repoUrl: string,
    skillNames: string[],
    branch: string = "main",
  ): Promise<{
    message: string;
    installed: string[];
    errors: string[];
  }> {
    return authFetch(`${API_BASE}/api/github/install`, {
      method: "POST",
      body: JSON.stringify({
        repo_url: repoUrl,
        branch,
        skill_names: skillNames,
      }),
    });
  },

  /**
   * Batch delete skills
   */
  async batchDelete(names: string[]): Promise<{
    deleted: string[];
    errors: Array<{ name: string; reason: string }>;
  }> {
    return authFetch(`${SKILLS_API}/batch/delete`, {
      method: "POST",
      body: JSON.stringify({ names }),
    });
  },

  /**
   * Batch toggle skills enabled state
   */
  async batchToggle(
    names: string[],
    enabled: boolean,
  ): Promise<{
    updated: string[];
    errors: Array<{ name: string; reason: string }>;
  }> {
    return authFetch(`${SKILLS_API}/batch/toggle`, {
      method: "POST",
      body: JSON.stringify({ names, enabled }),
    });
  },

  /**
   * Publish skill to marketplace
   */
  async publishToMarketplace(
    skillName: string,
    data?: PublishToMarketplaceRequest,
  ): Promise<MarketplaceSkillResponse> {
    return authFetch(`${SKILLS_API}/${encodeURIComponent(skillName)}/publish`, {
      method: "POST",
      body: JSON.stringify(data || {}),
    });
  },
};
