/**
 * useSkills hook - Simplified Architecture
 *
 * New backend stores skills as individual files. This hook:
 * - Fetches skill list from /api/skills/ (basic info only)
 * - Fetches full skill details (with files) on demand
 * - Composes SkillResponse for frontend components
 */

import { useState, useCallback, useEffect, useRef } from "react";
import i18n from "../i18n";
import { ApiRequestError } from "../services/api/fetch";
import { skillApi } from "../services/api/skill";
import type { SkillListParams } from "../services/api/skill";
import type {
  PublicSkillResponse,
  SkillResponse,
  SkillSource,
  UserSkill,
  UserSkillDetail,
  BinaryFileInfo,
} from "../types/skill";

// Map installed_from to SkillSource
function mapInstalledToSource(installed_from: string): SkillSource {
  switch (installed_from) {
    case "marketplace":
      return "marketplace";
    case "manual":
    default:
      return "manual";
  }
}

// Compose full SkillResponse from UserSkill + files content
function composeSkillResponse(
  userSkill: UserSkill,
  detail?: UserSkillDetail,
  filesContent?: Record<string, string>,
  binaryFiles?: Record<string, BinaryFileInfo>,
): PublicSkillResponse {
  // Use description from API directly (extracted from SKILL.md by backend)
  const description =
    detail?.description || userSkill.description || userSkill.skill_name;

  // If filesContent provided, use it; otherwise files will be fetched on demand
  const files = filesContent || {};

  // Prefer detail tags (from GET /{name}) over list tags (from GET /)
  const tags = detail?.tags ?? userSkill.tags ?? [];

  return {
    name: userSkill.skill_name,
    expected_version:
      detail?.expected_version || userSkill.expected_version,
    input_modes: detail?.input_modes ?? userSkill.input_modes,
    requires_file: detail?.requires_file ?? userSkill.requires_file,
    description,
    tags,
    enabled: userSkill.enabled,
    source: mapInstalledToSource(userSkill.installed_from),
    content: files["SKILL.md"] || "",
    files,
    binaryFiles: binaryFiles || {},
    file_count: userSkill.file_count,
    installed_from: userSkill.installed_from,
    published_marketplace_name: userSkill.published_marketplace_name,
    created_at: userSkill.created_at,
    updated_at: userSkill.updated_at,
    is_published: userSkill.is_published,
    marketplace_is_active: userSkill.marketplace_is_active,
  };
}

export function resolveExposedSkillPermissions({
  enabled,
  permissionsValid,
  effectivePermissions,
  effectivePermissionsKnown,
}: {
  enabled: boolean;
  permissionsValid: boolean;
  effectivePermissions: string[];
  effectivePermissionsKnown: boolean;
}): {
  effectivePermissions: string[];
  effectivePermissionsKnown: boolean;
} {
  if (!enabled || !permissionsValid) {
    return { effectivePermissions: [], effectivePermissionsKnown: false };
  }
  return { effectivePermissions, effectivePermissionsKnown };
}

/** Clear stale identities when the complete authorized catalog cannot load. */
export function resolveSkillsAfterListFailure(
  current: PublicSkillResponse[],
  allAuthorizedCatalog: boolean,
): PublicSkillResponse[] {
  return allAuthorizedCatalog ? [] : current;
}

export function resolveSkillOperationError(
  error: unknown,
  fallbackKey: string,
): string {
  if (error instanceof ApiRequestError && error.status === 403) {
    return i18n.t("errors.noPermission");
  }
  return i18n.t(fallbackKey);
}

export function useSkills(options?: {
  enabled?: boolean;
  listParams?: SkillListParams;
  allAuthorizedCatalog?: boolean;
}) {
  const enabled = options?.enabled !== false; // Default to true
  const listParams = options?.listParams;
  const allAuthorizedCatalog = options?.allAuthorizedCatalog === true;
  const [skills, setSkills] = useState<PublicSkillResponse[]>([]);
  const [availableTags, setAvailableTags] = useState<string[]>([]);
  const [effectivePermissions, setEffectivePermissions] = useState<string[]>(
    [],
  );
  const [effectivePermissionsKnown, setEffectivePermissionsKnown] =
    useState(false);
  const [catalogReadResolved, setCatalogReadResolved] = useState(false);
  const [permissionsValid, setPermissionsValid] = useState(false);
  const [total, setTotal] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);

  // Per-operation loading states for better UX
  const [isUpdating, setIsUpdating] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUploading, setIsUploading] = useState(false);

  // 跟踪正在 toggle 中的 skill，防止 fetchSkills 覆盖乐观更新
  const pendingTogglesRef = useRef<Map<string, boolean>>(new Map());
  // Only the newest catalog request may update visible state. Search, filters,
  // and pagination can otherwise resolve out of order and restore stale rows.
  const catalogRequestSequenceRef = useRef(0);
  const listParamsRef = useRef(listParams);
  listParamsRef.current = listParams;
  // Archive mutations own the catalog while they are pending. Reads that
  // started before or during an archive must not restore retained history to
  // the active catalog; the final mutation refresh supplies one authoritative
  // rows-and-total snapshot.
  const catalogMutationRevisionRef = useRef(0);
  const pendingCatalogMutationsRef = useRef(0);

  const beginCatalogMutation = useCallback(() => {
    pendingCatalogMutationsRef.current += 1;
    catalogMutationRevisionRef.current += 1;
    catalogRequestSequenceRef.current += 1;
  }, []);

  const finishCatalogMutation = useCallback(() => {
    pendingCatalogMutationsRef.current = Math.max(
      0,
      pendingCatalogMutationsRef.current - 1,
    );
    catalogMutationRevisionRef.current += 1;
    return pendingCatalogMutationsRef.current === 0;
  }, []);

  // Fetch all skills (basic info only)
  const fetchSkills = useCallback(
    async (params?: SkillListParams): Promise<boolean> => {
      if (!enabled) return false;
      const requestSequence = ++catalogRequestSequenceRef.current;
      const mutationRevision = catalogMutationRevisionRef.current;
      setIsLoading(true);
      setError(null);
      setListError(null);
      setCatalogReadResolved(false);
      setPermissionsValid(false);
      setEffectivePermissionsKnown(false);
      try {
        const response = allAuthorizedCatalog
          ? await skillApi.listAllAuthorized()
          : await skillApi.list(params ?? listParamsRef.current ?? {});
        const userSkills: UserSkill[] = response.skills;
        if (
          requestSequence !== catalogRequestSequenceRef.current ||
          mutationRevision !== catalogMutationRevisionRef.current ||
          pendingCatalogMutationsRef.current > 0
        ) {
          return false;
        }
        // For list view, we don't fetch full details immediately
        // Components that need details will fetch them on demand
        const composed = userSkills.map((u) => composeSkillResponse(u));
        setTotal(response.total);
        setAvailableTags(response.available_tags || []);
        setEffectivePermissions(response.effective_permissions || []);
        setCatalogReadResolved(response.catalog_read_resolved);
        setPermissionsValid(true);
        setEffectivePermissionsKnown(response.effective_permissions_known);
        // 保留正在 toggle 中的 skill 的乐观状态，避免竞态覆盖
        const pendingToggles = pendingTogglesRef.current;
        if (pendingToggles.size === 0) {
          setSkills(composed);
        } else {
          setSkills(
            composed.map((s) => {
              const pendingEnabled = pendingToggles.get(s.name);
              if (pendingEnabled !== undefined) {
                return { ...s, enabled: pendingEnabled };
              }
              return s;
            }),
          );
        }
        return true;
      } catch (err) {
        if (
          requestSequence !== catalogRequestSequenceRef.current ||
          mutationRevision !== catalogMutationRevisionRef.current ||
          pendingCatalogMutationsRef.current > 0
        ) {
          return false;
        }
        const message = resolveSkillOperationError(err, "skills.loadFailed");
        setError(message);
        setListError(message);
        setEffectivePermissions([]);
        setCatalogReadResolved(false);
        setPermissionsValid(true);
        setEffectivePermissionsKnown(true);
        setSkills((current) =>
          resolveSkillsAfterListFailure(current, allAuthorizedCatalog),
        );
        if (allAuthorizedCatalog) {
          setTotal(0);
          setAvailableTags([]);
        }
        return false;
      } finally {
        if (requestSequence === catalogRequestSequenceRef.current) {
          setIsLoading(false);
        }
      }
    },
    [allAuthorizedCatalog, enabled],
  );

  // Fetch single skill — metadata + file paths only (lazy: content loaded on demand)
  const getSkill = useCallback(
    async (name: string): Promise<SkillResponse | null> => {
      if (!enabled) return null;
      try {
        // Use cached skills list first, then fetch detail
        const cached = skills.find((s) => s.name === name);
        const detail = await skillApi.get(name);

        // Build UserSkill from cached list or fetch it
        let userSkill: UserSkill | null = null;
        if (cached) {
          userSkill = {
            skill_name: cached.name,
            expected_version: cached.expected_version,
            input_modes: cached.input_modes,
            requires_file: cached.requires_file,
            description: cached.description,
            tags: cached.tags,
            files: cached.filePaths || [],
            enabled: cached.enabled,
            file_count: cached.file_count,
            installed_from: cached.installed_from,
            published_marketplace_name: cached.published_marketplace_name,
            created_at: cached.created_at,
            updated_at: cached.updated_at,
            is_published: cached.is_published,
            marketplace_is_active: cached.marketplace_is_active,
          };
        } else {
          userSkill = {
            skill_name: detail.skill_name || name,
            expected_version: detail.expected_version || "",
            input_modes: detail.input_modes || [],
            requires_file: detail.requires_file ?? false,
            description: detail.description || name,
            tags: detail.tags || [],
            files: detail.files || [],
            enabled: detail.enabled ?? true,
            file_count: detail.files?.length || 0,
            installed_from: "manual",
            created_at: undefined,
            updated_at: undefined,
            is_published: detail.is_published || false,
            marketplace_is_active: detail.marketplace_is_active ?? true,
          };
        }

        const resp = composeSkillResponse(userSkill, detail);
        resp.filePaths = detail.files || [];
        return resp;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch skill");
        return null;
      }
    },
    [enabled, skills],
  );

  // Fetch single skill with ALL file contents (for export etc.)
  const getFullSkill = useCallback(
    async (name: string): Promise<SkillResponse | null> => {
      if (!enabled) return null;
      try {
        // Use cached skills list first
        const cached = skills.find((s) => s.name === name);
        const detail = await skillApi.get(name);

        let userSkill: UserSkill | null = null;
        if (cached) {
          userSkill = {
            skill_name: cached.name,
            expected_version: cached.expected_version,
            input_modes: cached.input_modes,
            requires_file: cached.requires_file,
            description: cached.description,
            tags: cached.tags,
            files: cached.filePaths || [],
            enabled: cached.enabled,
            file_count: cached.file_count,
            installed_from: cached.installed_from,
            published_marketplace_name: cached.published_marketplace_name,
            created_at: cached.created_at,
            updated_at: cached.updated_at,
            is_published: cached.is_published,
            marketplace_is_active: cached.marketplace_is_active,
          };
        } else {
          userSkill = {
            skill_name: detail.skill_name || name,
            expected_version: detail.expected_version || "",
            input_modes: detail.input_modes || [],
            requires_file: detail.requires_file ?? false,
            description: detail.description || name,
            tags: detail.tags || [],
            files: detail.files || [],
            enabled: detail.enabled ?? true,
            file_count: detail.files?.length || 0,
            installed_from: "manual",
            created_at: undefined,
            updated_at: undefined,
            is_published: detail.is_published || false,
            marketplace_is_active: detail.marketplace_is_active ?? true,
          };
        }

        const filesContent: Record<string, string> = {};
        const binaryFiles: Record<string, BinaryFileInfo> = {};
        if (detail.files) {
          await Promise.all(
            detail.files.map(async (filePath) => {
              try {
                const fileResp = await skillApi.getFile(name, filePath);
                if (fileResp.is_binary && fileResp.url) {
                  filesContent[filePath] = `[Binary: ${fileResp.mime_type}, ${(
                    (fileResp.size ?? 0) / 1024
                  ).toFixed(1)}KB]`;
                  binaryFiles[filePath] = {
                    url: fileResp.url,
                    mime_type: fileResp.mime_type || "application/octet-stream",
                    size: fileResp.size || 0,
                  };
                } else {
                  filesContent[filePath] = fileResp.content;
                }
              } catch {
                // File might not be readable
              }
            }),
          );
        }

        return composeSkillResponse(
          userSkill,
          detail,
          filesContent,
          binaryFiles,
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch skill");
        return null;
      }
    },
    [enabled, skills],
  );

  // Update skill
  const updateSkill = useCallback(
    async (
      name: string,
      updates: {
        description?: string;
        content?: string;
        enabled?: boolean;
        files?: Record<string, string>;
        deletedFiles?: string[];
      },
    ): Promise<boolean> => {
      if (!enabled) return false;
      setIsUpdating(true);
      setError(null);
      try {
        await skillApi.update(name, updates);
        await fetchSkills();
        return true;
      } catch (err) {
        setError(resolveSkillOperationError(err, "skills.updateFailed"));
        return false;
      } finally {
        setIsUpdating(false);
      }
    },
    [enabled, fetchSkills],
  );

  // Delete skill
  const deleteSkill = useCallback(
    async (name: string): Promise<boolean> => {
      if (!enabled) return false;
      const archivedSkill = skills.find((skill) => skill.name === name);
      const archivedIndex = skills.findIndex((skill) => skill.name === name);
      let archiveError: string | null = null;
      beginCatalogMutation();
      setIsDeleting(true);
      setError(null);
      if (archivedSkill) {
        setSkills((current) =>
          current.filter((skill) => skill.name !== name),
        );
        setTotal((current) => Math.max(0, current - 1));
      }
      try {
        await skillApi.delete(name);
        return true;
      } catch (err) {
        if (archivedSkill) {
          setSkills((current) => {
            if (current.some((skill) => skill.name === name)) return current;
            const restored = [...current];
            restored.splice(
              Math.min(Math.max(archivedIndex, 0), restored.length),
              0,
              archivedSkill,
            );
            return restored;
          });
          setTotal((current) => current + 1);
        }
        archiveError = resolveSkillOperationError(err, "skills.deleteFailed");
        setError(archiveError);
        return false;
      } finally {
        if (finishCatalogMutation()) {
          await fetchSkills();
        }
        if (archiveError) setError(archiveError);
        setIsDeleting(false);
      }
    },
    [beginCatalogMutation, enabled, fetchSkills, finishCatalogMutation, skills],
  );

  // Toggle skill
  const toggleSkill = useCallback(
    async (name: string): Promise<boolean> => {
      if (!enabled) return false;
      // 记录期望的 toggle 状态
      const currentSkill = skills.find((s) => s.name === name);
      if (!currentSkill) {
        return false;
      }
      const newEnabled = currentSkill ? !currentSkill.enabled : true;
      pendingTogglesRef.current.set(name, newEnabled);

      // Optimistic update
      setSkills((prev) =>
        prev.map((s) => (s.name === name ? { ...s, enabled: newEnabled } : s)),
      );

      try {
        const result = await skillApi.toggle(name, newEnabled);
        setSkills((prev) =>
          prev.map((s) =>
            s.name === name ? { ...s, enabled: result.enabled } : s,
          ),
        );
        return true;
      } catch (err) {
        // Rollback on error
        pendingTogglesRef.current.delete(name);
        setSkills((prev) =>
          prev.map((s) =>
            s.name === name ? { ...s, enabled: !newEnabled } : s,
          ),
        );
        setError(resolveSkillOperationError(err, "skills.toggleFailed"));
        return false;
      } finally {
        // toggle 完成后清除 pending 状态
        pendingTogglesRef.current.delete(name);
      }
    },
    [enabled, skills],
  );

  // Batch delete skills
  const batchDeleteSkills = useCallback(
    async (names: string[]): Promise<string[]> => {
      if (!enabled) return [];
      let archiveError: string | null = null;
      beginCatalogMutation();
      setError(null);
      try {
        const result = await skillApi.batchDelete(names);
        // Optimistic remove already-deleted skills from state
        if (result.deleted.length > 0) {
          setSkills((prev) =>
            prev.filter((s) => !result.deleted.includes(s.name)),
          );
          setTotal((current) =>
            Math.max(0, current - result.deleted.length),
          );
        }
        return result.deleted;
      } catch (err) {
        archiveError =
          err instanceof Error ? err.message : "Failed to delete skills";
        setError(archiveError);
        return [];
      } finally {
        if (finishCatalogMutation()) {
          await fetchSkills();
        }
        if (archiveError) setError(archiveError);
      }
    },
    [beginCatalogMutation, enabled, fetchSkills, finishCatalogMutation],
  );

  // Batch toggle skills
  const batchToggleSkills = useCallback(
    async (names: string[], nextEnabled: boolean): Promise<boolean> => {
      if (!enabled) return false;
      // Optimistic update
      names.forEach((name) => pendingTogglesRef.current.set(name, nextEnabled));
      setSkills((prev) =>
        prev.map((s) =>
          names.includes(s.name) ? { ...s, enabled: nextEnabled } : s,
        ),
      );

      try {
        const result = await skillApi.batchToggle(names, nextEnabled);
        // Clear pending for successful ones
        result.updated.forEach((name) =>
          pendingTogglesRef.current.delete(name),
        );
        // Refresh for consistency
        await fetchSkills();
        return result.errors.length === 0;
      } catch (err) {
        // Rollback on error
        names.forEach((name) => pendingTogglesRef.current.delete(name));
        setSkills((prev) =>
          prev.map((s) =>
            names.includes(s.name) ? { ...s, enabled: !nextEnabled } : s,
          ),
        );
        setError(
          err instanceof Error ? err.message : "Failed to toggle skills",
        );
        return false;
      }
    },
    [enabled, fetchSkills],
  );

  // Toggle category (not applicable in new architecture - just toggle all)
  const toggleCategory = useCallback(
    async (_category: SkillSource, nextEnabled: boolean): Promise<boolean> => {
      if (!enabled) return false;
      const names = skills
        .filter((s) => s.source === _category && s.enabled !== nextEnabled)
        .map((s) => s.name);
      if (names.length === 0) {
        return true;
      }
      return await batchToggleSkills(names, nextEnabled);
    },
    [batchToggleSkills, enabled, skills],
  );

  // Toggle all skills
  const toggleAll = useCallback(
    async (nextEnabled: boolean): Promise<boolean> => {
      if (!enabled) return false;
      const names = skills
        .filter((s) => s.enabled !== nextEnabled)
        .map((s) => s.name);
      if (names.length === 0) {
        return true;
      }
      return await batchToggleSkills(names, nextEnabled);
    },
    [batchToggleSkills, enabled, skills],
  );

  // Get enabled skill names
  const getEnabledSkillNames = useCallback((): string[] => {
    return skills.filter((s) => s.enabled).map((s) => s.name);
  }, [skills]);

  // Get category stats
  const getCategoryStats = useCallback(() => {
    const stats: Record<SkillSource, { enabled: number; total: number }> = {
      marketplace: { enabled: 0, total: 0 },
      manual: { enabled: 0, total: 0 },
    };

    skills.forEach((skill) => {
      const cat = skill.source;
      if (stats[cat]) {
        stats[cat].total++;
        if (skill.enabled) {
          stats[cat].enabled++;
        }
      }
    });

    return stats;
  }, [skills]);

  // Upload skill(s) from ZIP file
  const uploadSkill = useCallback(
    async (
      file: File,
      skillNames?: string[],
    ): Promise<{
      created: Array<{ name: string; file_count: number }>;
      errors: Array<{ name: string; reason: string }>;
    } | null> => {
      if (!enabled) return null;
      setIsUploading(true);
      setError(null);
      try {
        const result = await skillApi.uploadZip(file, skillNames);
        await fetchSkills();
        return result;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to upload skill");
        return null;
      } finally {
        setIsUploading(false);
      }
    },
    [enabled, fetchSkills],
  );

  // Upload an immutable governed Skill draft. Review and promotion are explicit
  // follow-up calls so a new draft never has to be rediscovered from the public
  // catalog before it can be released.
  const adminUploadSkill = useCallback(
    async (
      file: File,
      skillName: string,
    ): Promise<Awaited<ReturnType<typeof skillApi.adminUploadZip>> | null> => {
      if (!enabled) return null;
      setIsUploading(true);
      setError(null);
      try {
        return await skillApi.adminUploadZip(skillName, file);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to upload admin skill",
        );
        return null;
      } finally {
        setIsUploading(false);
      }
    },
    [enabled],
  );

  const adminReviewSkillVersion = useCallback(
    async (
      skillName: string,
      version: string,
    ): Promise<Awaited<ReturnType<typeof skillApi.adminReviewSkillVersion>> | null> => {
      if (!enabled) return null;
      setIsUploading(true);
      setError(null);
      try {
        return await skillApi.adminReviewSkillVersion(skillName, version);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to review admin skill",
        );
        return null;
      } finally {
        setIsUploading(false);
      }
    },
    [enabled],
  );

  const adminPromoteSkillVersion = useCallback(
    async (
      skillName: string,
      version: string,
    ): Promise<Awaited<ReturnType<typeof skillApi.adminPromoteSkillVersion>> | null> => {
      if (!enabled) return null;
      setIsUploading(true);
      setError(null);
      try {
        return await skillApi.adminPromoteSkillVersion(skillName, version);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to promote admin skill",
        );
        return null;
      } finally {
        setIsUploading(false);
      }
    },
    [enabled],
  );

  /** Read the admin-safe lifecycle catalog, including drafts not yet public. */
  const adminListSkills = useCallback(
    async (): Promise<Awaited<ReturnType<typeof skillApi.adminListSkills>> | null> => {
      if (!enabled) return null;
      setError(null);
      try {
        return await skillApi.adminListSkills();
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to refresh admin skill catalog",
        );
        return null;
      }
    },
    [enabled],
  );

  // Preview skills from ZIP file
  const previewZipSkills = useCallback(
    async (
      file: File,
    ): Promise<{
      skill_count: number;
      skills: Array<{
        name: string;
        description: string;
        file_count: number;
        files: string[];
        already_exists: boolean;
      }>;
    } | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        return await skillApi.previewZip(file);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to preview ZIP");
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled],
  );

  // Preview skills from ZIP file through the admin path with global catalog checks.
  const adminPreviewZipSkills = useCallback(
    async (
      file: File,
    ): Promise<{
      skill_count: number;
      skills: Array<{
        name: string;
        description: string;
        file_count: number;
        files: string[];
        already_exists: boolean;
      }>;
    } | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        return await skillApi.adminPreviewZip(file);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to preview admin ZIP",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled],
  );

  // Preview skills from GitHub repository
  const previewGitHubSkills = useCallback(
    async (
      repoUrl: string,
      branch: string = "main",
    ): Promise<{
      repo_url: string;
      branch: string;
      skills: Array<{ name: string; path: string; description: string }>;
    } | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        return await skillApi.previewGitHub(repoUrl, branch);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to preview GitHub skills",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled],
  );

  // Install skills from GitHub repository
  const installGitHubSkills = useCallback(
    async (
      repoUrl: string,
      skillNames: string[],
      branch: string = "main",
    ): Promise<{
      message: string;
      installed: string[];
      errors: string[];
    } | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const result = await skillApi.installGitHub(
          repoUrl,
          skillNames,
          branch,
        );
        await fetchSkills();
        return result;
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Failed to install GitHub skills",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchSkills],
  );

  // Stats
  const enabledCount = skills.filter((s) => s.enabled).length;
  const totalCount = skills.length;
  const pendingSkillNames = Array.from(pendingTogglesRef.current.keys());
  const isMutating = pendingSkillNames.length > 0;
  const exposedPermissions = resolveExposedSkillPermissions({
    enabled,
    permissionsValid,
    effectivePermissions,
    effectivePermissionsKnown,
  });

  // Initial load
  const catalogLoadParams = allAuthorizedCatalog ? undefined : listParams;

  useEffect(() => {
    void fetchSkills(catalogLoadParams);
  }, [catalogLoadParams, fetchSkills]);

  return {
    skills,
    availableTags,
    effectivePermissions: exposedPermissions.effectivePermissions,
    effectivePermissionsKnown: exposedPermissions.effectivePermissionsKnown,
    catalogReadResolved,
    total,
    isLoading,
    error,
    listError,
    fetchSkills,
    getSkill,
    getFullSkill,
    updateSkill,
    deleteSkill,
    batchDeleteSkills,
    batchToggleSkills,
    toggleSkill,
    toggleCategory,
    toggleAll,
    uploadSkill,
    adminUploadSkill,
    adminReviewSkillVersion,
    adminPromoteSkillVersion,
    adminListSkills,
    previewZipSkills,
    adminPreviewZipSkills,
    previewGitHubSkills,
    installGitHubSkills,
    pendingSkillNames,
    isMutating,
    isUpdating,
    isDeleting,
    isUploading,
    getEnabledSkillNames,
    getCategoryStats,
    enabledCount,
    totalCount,
    clearError: () => setError(null),
  };
}
