/**
 * MCP API - MCP Server Management
 */

import type { MCPToolDiscoveryResponse } from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export const mcpApi = {
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
