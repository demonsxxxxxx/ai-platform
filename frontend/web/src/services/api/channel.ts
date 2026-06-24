/**
 * Governed channel API projection.
 */

import { API_BASE } from "./config";
import { authFetch } from "./fetch";
import type {
  ChannelAdminOperationResponse,
  PublicChannelsResponse,
} from "../../types/channel";

export const channelApi = {
  /**
   * List tenant-scoped channel catalog items without secret material.
   */
  async listCatalog(workspaceId = "default"): Promise<PublicChannelsResponse> {
    const params = new URLSearchParams();
    if (workspaceId) {
      params.set("workspace_id", workspaceId);
    }
    const query = params.toString();
    return authFetch<PublicChannelsResponse>(
      `${API_BASE}/api/channels/catalog${query ? `?${query}` : ""}`,
    );
  },
};

export const channelAdminApi = {
  /**
   * Queue an audited admin dry-run test for a channel.
   */
  async testAdminChannel(
    channelId: string,
    workspaceId = "default",
  ): Promise<ChannelAdminOperationResponse> {
    const params = new URLSearchParams();
    if (workspaceId) {
      params.set("workspace_id", workspaceId);
    }
    const query = params.toString();
    return authFetch<ChannelAdminOperationResponse>(
      `${API_BASE}/api/admin/channels/${encodeURIComponent(
        channelId,
      )}/test${query ? `?${query}` : ""}`,
      {
        method: "POST",
        body: JSON.stringify({ dry_run: true }),
      },
    );
  },
};
