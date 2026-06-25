import { useState, useCallback, useEffect } from "react";
import { marketplaceApi } from "../services/api/marketplace";
import type {
  MarketplaceSkillResponse,
  MarketplaceSkillFilesResponse,
  MarketplaceCreateRequest,
} from "../types";

interface BinaryFileInfo {
  url: string;
  mime_type: string;
  size: number;
}

export function useMarketplace(options?: { enabled?: boolean }) {
  const enabled = options?.enabled !== false;
  const [skills, setSkills] = useState<MarketplaceSkillResponse[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [effectivePermissions, setEffectivePermissions] = useState<string[]>(
    [],
  );
  const [effectivePermissionsKnown, setEffectivePermissionsKnown] =
    useState(false);
  const [catalogReadResolved, setCatalogReadResolved] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");

  // Debounced search value for API calls
  const [debouncedSearch, setDebouncedSearch] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Preview state
  const [previewSkill, setPreviewSkill] =
    useState<MarketplaceSkillResponse | null>(null);
  const [previewFiles, setPreviewFiles] =
    useState<MarketplaceSkillFilesResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewFileContent, setPreviewFileContent] = useState<
    Record<string, string>
  >({});
  const [previewBinaryFiles, setPreviewBinaryFiles] = useState<
    Record<string, BinaryFileInfo>
  >({});
  const [previewFileLoading, setPreviewFileLoading] = useState<string | null>(
    null,
  );

  // Fetch marketplace skills
  const fetchSkills = useCallback(async () => {
    if (!enabled) return;
    setIsLoading(true);
    setError(null);
    setListError(null);
    try {
      const tagsParam =
        selectedTags.length > 0 ? selectedTags.join(",") : undefined;
      const data = await marketplaceApi.list({
        tags: tagsParam,
        search: debouncedSearch || undefined,
      });
      setSkills(data.skills ?? []);
      setEffectivePermissions(data.effective_permissions ?? []);
      setEffectivePermissionsKnown(data.effective_permissions_known);
      setCatalogReadResolved(data.catalog_read_resolved);
      if (data.available_tags.length > 0) {
        setTags(data.available_tags);
      }
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
          : "Failed to fetch marketplace skills";
      setError(message);
      setListError(message);
      setEffectivePermissions([]);
      setEffectivePermissionsKnown(false);
      setCatalogReadResolved(false);
    } finally {
      setIsLoading(false);
    }
  }, [debouncedSearch, enabled, selectedTags]);

  // Fetch all tags
  const fetchTags = useCallback(async () => {
    if (!enabled) return;
    try {
      const data = await marketplaceApi.getTags();
      setTags(data.tags ?? []);
    } catch (err) {
      console.error("Failed to fetch tags:", err);
    }
  }, [enabled]);

  // Install a skill
  const installSkill = useCallback(
    async (skillName: string): Promise<boolean> => {
      if (!enabled) return false;
      try {
        await marketplaceApi.install(skillName);
        return true;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to install skill",
        );
        return false;
      }
    },
    [enabled],
  );

  // Update a skill from marketplace
  const updateSkill = useCallback(
    async (skillName: string): Promise<boolean> => {
      if (!enabled) return false;
      try {
        await marketplaceApi.update(skillName);
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to update skill");
        return false;
      }
    },
    [enabled],
  );

  // Preview skill detail
  const openPreview = useCallback(async (skill: MarketplaceSkillResponse) => {
    if (!enabled) return;
    setPreviewSkill(skill);
    setPreviewFiles(null);
    setPreviewFileContent({});
    setPreviewBinaryFiles({});
    setPreviewLoading(true);
    try {
      const files = await marketplaceApi.listFiles(skill.skill_name);
      setPreviewFiles(files);
    } catch (err) {
      console.error("Failed to fetch skill files:", err);
    } finally {
      setPreviewLoading(false);
    }
  }, [enabled]);

  // Read preview file content
  const readPreviewFile = useCallback(
    async (skillName: string, filePath: string) => {
      if (!enabled) return;
      setPreviewFileLoading(filePath);
      try {
        const resp = await marketplaceApi.getFile(skillName, filePath);
        setPreviewFileContent((prev) => ({
          ...prev,
          [filePath]: resp.content,
        }));
        if (resp.is_binary && resp.url && resp.mime_type && resp.size !== undefined) {
          setPreviewBinaryFiles((prev) => ({
            ...prev,
            [filePath]: {
              url: resp.url!,
              mime_type: resp.mime_type!,
              size: resp.size!,
            },
          }));
        }
      } catch (err) {
        console.error("Failed to fetch file content:", err);
      } finally {
        setPreviewFileLoading(null);
      }
    },
    [enabled],
  );

  const closePreview = useCallback(() => {
    setPreviewSkill(null);
    setPreviewFiles(null);
    setPreviewFileContent({});
    setPreviewBinaryFiles({});
  }, []);

  // Toggle tag selection
  const toggleTag = useCallback((tag: string) => {
    setSelectedTags((prev) =>
      prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag],
    );
  }, []);

  // Clear filters
  const clearFilters = useCallback(() => {
    setSelectedTags([]);
    setSearchQuery("");
    setDebouncedSearch("");
  }, []);

  // Create and publish skill directly in marketplace
  const createAndPublish = useCallback(
    async (data: MarketplaceCreateRequest): Promise<boolean> => {
      if (!enabled) return false;
      setIsLoading(true);
      setError(null);
      try {
        await marketplaceApi.createAndPublish(data);
        await fetchSkills();
        await fetchTags();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create skill");
        return false;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchSkills, fetchTags],
  );

  // Update marketplace skill directly (creator only)
  const updateMarketplaceSkill = useCallback(
    async (
      skillName: string,
      data: MarketplaceCreateRequest,
    ): Promise<boolean> => {
      if (!enabled) return false;
      setIsLoading(true);
      setError(null);
      try {
        await marketplaceApi.updateMarketplaceSkill(skillName, data);
        await fetchSkills();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to update skill");
        return false;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchSkills],
  );

  // Admin: activate/deactivate skill
  const activateSkill = useCallback(
    async (skillName: string, isActive: boolean): Promise<boolean> => {
      if (!enabled) return false;
      setError(null);
      try {
        await marketplaceApi.activate(skillName, isActive);
        await fetchSkills();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to update skill");
        return false;
      }
    },
    [enabled, fetchSkills],
  );

  // Admin: delete skill from marketplace
  const deleteSkill = useCallback(
    async (skillName: string): Promise<boolean> => {
      if (!enabled) return false;
      setError(null);
      try {
        await marketplaceApi.deleteSkill(skillName);
        await fetchSkills();
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete skill");
        return false;
      }
    },
    [enabled, fetchSkills],
  );

  // Load marketplace skill for editing (without local copy)
  const loadMarketplaceSkillForEdit = useCallback(async (skillName: string) => {
    if (!enabled) return null;
    try {
      const [filesResp, skillDetail] = await Promise.all([
        marketplaceApi.listFiles(skillName),
        marketplaceApi.get(skillName),
      ]);

      const fileContents: Record<string, string> = {};
      await Promise.all(
        filesResp.files.map(async (path) => {
          const resp = await marketplaceApi.getFile(skillName, path);
          fileContents[path] = resp.content;
        }),
      );

      return {
        name: skillName,
        description: skillDetail.description,
        tags: skillDetail.tags,
        content: fileContents["SKILL.md"] || "",
        files: fileContents,
        enabled: true,
        source: "marketplace" as const,
        file_count: filesResp.files.length,
        installed_from: "marketplace" as const,
        is_published: true,
        marketplace_is_active: skillDetail.is_active,
      };
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to load marketplace skill",
      );
      return null;
    }
  }, [enabled]);

  // Initial load
  useEffect(() => {
    if (!enabled) return;
    fetchSkills();
  }, [enabled, fetchSkills]);

  // Load tags on mount
  useEffect(() => {
    if (!enabled) return;
    fetchTags();
  }, [enabled, fetchTags]);

  return {
    skills,
    tags,
    effectivePermissions,
    effectivePermissionsKnown,
    catalogReadResolved,
    isLoading,
    error,
    listError,
    selectedTags,
    searchQuery,
    setSearchQuery,
    toggleTag,
    clearFilters,
    fetchSkills,
    installSkill,
    updateSkill,
    createAndPublish,
    updateMarketplaceSkill,
    activateSkill,
    deleteSkill,
    loadMarketplaceSkillForEdit,
    clearError: () => setError(null),
    // Preview
    previewSkill,
    previewFiles,
    previewLoading,
    previewFileContent,
    previewBinaryFiles,
    previewFileLoading,
    openPreview,
    readPreviewFile,
    closePreview,
    setPreviewFileContent,
  };
}
