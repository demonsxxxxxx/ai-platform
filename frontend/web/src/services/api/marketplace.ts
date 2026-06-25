/**
 * Marketplace API - Skills 商城
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  MarketplaceListResponse,
  MarketplaceSkillResponse,
  MarketplaceSkillFilesResponse,
  MarketplaceSkillFileResponse,
  MarketplaceInstallResponse,
  MarketplaceCreateRequest,
  TagsResponse,
} from "../../types";

const MARKETPLACE_API = `${API_BASE}/api/marketplace`;

type MarketplaceListWireResponse =
  | MarketplaceSkillResponse[]
  | Omit<MarketplaceListResponse, "effective_permissions_known">;

/**
 * Build the authenticated public Marketplace list URL used by the post-login catalog.
 */
export function buildMarketplaceListUrl(params?: {
  tags?: string;
  search?: string;
  skip?: number;
  limit?: number;
}): string {
  const searchParams = new URLSearchParams();
  if (params?.tags) searchParams.set("tags", params.tags);
  if (params?.search) searchParams.set("search", params.search);
  if (params?.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params?.limit !== undefined)
    searchParams.set("limit", String(params.limit));

  const query = searchParams.toString();
  return `${MARKETPLACE_API}/${query ? `?${query}` : ""}`;
}

export function normalizeMarketplaceListResponse(
  response: MarketplaceListWireResponse,
): MarketplaceListResponse {
  if (Array.isArray(response)) {
    return {
      skills: response,
      total: response.length,
      skip: 0,
      limit: response.length,
      available_tags: [],
      effective_permissions: [],
      effective_permissions_known: false,
    };
  }

  return {
    skills: response.skills ?? [],
    total: response.total ?? response.skills?.length ?? 0,
    skip: response.skip ?? 0,
    limit: response.limit ?? response.skills?.length ?? 0,
    available_tags: response.available_tags ?? [],
    effective_permissions: response.effective_permissions ?? [],
    effective_permissions_known: Array.isArray(response.effective_permissions),
  };
}

export const marketplaceApi = {
  /**
   * List all marketplace skills
   */
  async list(params?: {
    tags?: string;
    search?: string;
    skip?: number;
    limit?: number;
  }): Promise<MarketplaceListResponse> {
    const response = await authFetch<MarketplaceListWireResponse>(
      buildMarketplaceListUrl(params),
    );
    return normalizeMarketplaceListResponse(response ?? []);
  },

  /**
   * Get all available tags
   */
  async getTags() {
    return authFetch<TagsResponse>(`${MARKETPLACE_API}/tags`);
  },

  /**
   * Get marketplace skill details
   */
  async get(skillName: string) {
    return authFetch<MarketplaceSkillResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}`,
    );
  },

  /**
   * List marketplace skill files
   */
  async listFiles(skillName: string) {
    return authFetch<MarketplaceSkillFilesResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}/files`,
    );
  },

  /**
   * Get marketplace skill file content
   */
  async getFile(skillName: string, filePath: string) {
    return authFetch<MarketplaceSkillFileResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(
        skillName,
      )}/files/${encodeURIComponent(filePath)}`,
    );
  },

  /**
   * Install marketplace skill to user's account
   */
  async install(skillName: string) {
    return authFetch<MarketplaceInstallResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}/install`,
      {
        method: "POST",
      },
    );
  },

  /**
   * Update installed skill from marketplace (re-download)
   */
  async update(skillName: string) {
    return authFetch<MarketplaceInstallResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}/update`,
      {
        method: "POST",
      },
    );
  },

  /**
   * Create and publish skill directly in marketplace
   */
  async createAndPublish(data: MarketplaceCreateRequest) {
    return authFetch<MarketplaceSkillResponse>(`${MARKETPLACE_API}/`, {
      method: "POST",
      body: JSON.stringify(data),
    });
  },

  /**
   * Update marketplace skill directly (creator only)
   */
  async updateMarketplaceSkill(
    skillName: string,
    data: MarketplaceCreateRequest,
  ) {
    return authFetch<MarketplaceSkillResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}`,
      {
        method: "PUT",
        body: JSON.stringify(data),
      },
    );
  },

  /**
   * Admin: activate or deactivate a marketplace skill
   */
  async activate(skillName: string, isActive: boolean) {
    return authFetch<MarketplaceSkillResponse>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}/activate`,
      {
        method: "PATCH",
        body: JSON.stringify({ is_active: isActive }),
      },
    );
  },

  /**
   * Admin: delete a marketplace skill
   */
  async deleteSkill(skillName: string) {
    return authFetch<{ message: string }>(
      `${MARKETPLACE_API}/${encodeURIComponent(skillName)}`,
      {
        method: "DELETE",
      },
    );
  },
};
