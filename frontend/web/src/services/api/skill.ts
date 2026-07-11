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

export interface AdminSkillUploadResponse {
  uploaded: {
    skill_id: string;
    version: string;
    content_hash: string;
    description: string;
    source: Record<string, unknown>;
    dependency_ids: string[];
    status: string;
    created_by?: string | null;
    created_at?: unknown;
  };
}

type SkillListWireResponse =
  | UserSkill[]
  | (Omit<
      SkillsResponse,
      "effective_permissions_known" | "catalog_read_resolved"
    > & {
      catalog_read_resolved?: boolean;
    });

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

export function normalizeSkillListResponse(
  response: SkillListWireResponse,
): SkillsResponse {
  if (Array.isArray(response)) {
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

  const skills = response.skills ?? [];
  const catalogReadResolved =
    typeof response.catalog_read_resolved === "boolean"
      ? response.catalog_read_resolved
      : true;

  return {
    skills,
    total: response.total ?? skills.length,
    skip: response.skip ?? 0,
    limit: response.limit ?? skills.length,
    available_tags: response.available_tags ?? [],
    effective_permissions: response.effective_permissions ?? [],
    effective_permissions_known: Array.isArray(response.effective_permissions),
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

    if (skillsByName.size >= expectedTotal) break;
    if (page.skills.length === 0) {
      throw new Error("authorized_skill_catalog_incomplete");
    }
    if (skillsByName.size === priorUniqueCount) {
      throw new Error("authorized_skill_catalog_no_progress");
    }
    skip += page.skills.length;
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
  const response = await authFetch<SkillListWireResponse>(
    buildSkillListUrl(params),
  );
  return normalizeSkillListResponse(response ?? []);
}

async function listAllAuthorizedSkills(): Promise<SkillsResponse> {
  return collectAllAuthorizedSkills(listSkills);
}

export const skillApi = {
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
  async previewZip(file: File): Promise<{
    skill_count: number;
    skills: Array<{
      name: string;
      description: string;
      file_count: number;
      files: string[];
      already_exists: boolean;
    }>;
  }> {
    const formData = new FormData();
    formData.append("file", file);
    return authFetch(`${SKILLS_API}/upload/preview`, {
      method: "POST",
      body: formData,
    });
  },

  /**
   * Preview a ZIP file through the admin Skill path with global catalog checks.
   */
  async adminPreviewZip(file: File): Promise<{
    skill_count: number;
    skills: Array<{
      name: string;
      description: string;
      file_count: number;
      files: string[];
      already_exists: boolean;
    }>;
  }> {
    const formData = new FormData();
    formData.append("file", file);
    return authFetch(buildAdminSkillPreviewUrl(), {
      method: "POST",
      body: formData,
    });
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
    return authFetch(buildAdminSkillUploadUrl(skillName), {
      method: "POST",
      body: formData,
    });
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
