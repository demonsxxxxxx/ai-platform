/**
 * MCP API - MCP Server Management
 */

import type { MCPToolDiscoveryResponse } from "../../types";
import { API_BASE } from "./config";
import { authFetch } from "./fetch";

export const mcpApi = {
  /**
   * Discover tools from a specific MCP server
   */
  async discoverTools(name: string): Promise<MCPToolDiscoveryResponse> {
    return authFetch<MCPToolDiscoveryResponse>(
      `${API_BASE}/api/mcp/${encodeURIComponent(name)}/tools`,
    );
  },
};
