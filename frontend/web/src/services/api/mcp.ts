/**
 * MCP API - MCP Server Management
 */

import type { MCPToolDiscoveryResponse } from "../../types";
import type { MCPConnectionTestResponse, MCPPluginResponse } from "../../types/mcp";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export const mcpApi = {
  async getPlugins(skip = 0): Promise<{ servers: MCPPluginResponse[]; total: number; skip: number; limit: number }> {
    return authFetch(`${API_BASE}/api/mcp/?view=plugins&skip=${skip}&limit=200`);
  },

  async updateGatewayConversationPreference(name: string, enabled: boolean): Promise<{ use_in_conversation: boolean }> {
    return authFetch(`${API_BASE}/api/mcp/${encodeURIComponent(name)}/conversation-preference`, {
      method: "PUT",
      body: JSON.stringify({ use_in_conversation: enabled }),
    });
  },

  async testConnection(name: string): Promise<MCPConnectionTestResponse> {
    return authFetch(`${API_BASE}/api/admin/mcp/${encodeURIComponent(name)}/test`, { method: "POST" });
  },

  async getServerCatalog(name: string): Promise<MCPToolDiscoveryResponse> {
    return authFetch<MCPToolDiscoveryResponse>(`${API_BASE}/api/mcp/${encodeURIComponent(name)}/catalog`);
  },

  async getCatalogTools(): Promise<unknown> {
    return authFetch<unknown>(`${API_BASE}/api/mcp/catalog-tools`);
  },
  /**
   * Discover tools from a specific MCP server
   */
  async discoverTools(name: string): Promise<MCPToolDiscoveryResponse> {
    return authFetch<MCPToolDiscoveryResponse>(
      `${API_BASE}/api/mcp/${encodeURIComponent(name)}/tools`,
    );
  },
};
