import { useState, useCallback, useEffect, useRef } from "react";
import { authFetch } from "../services/api/fetch";
import type {
  MCPServerResponse,
  MCPServersResponse,
  MCPServerCreate,
  MCPServerUpdate,
  MCPServerToggleResponse,
  MCPImportRequest,
  MCPImportResponse,
  MCPExportResponse,
  MCPServerMoveResponse,
} from "../types";

const API_BASE = "/api/mcp";

interface MCPListParams {
  skip?: number;
  limit?: number;
  q?: string;
}

const AUTHORIZED_MCP_PAGE_LIMIT = 200;
const AUTHORIZED_MCP_MAX_PAGES = 1_000;

type McpPageLoader = (params: {
  skip: number;
  limit: number;
}) => Promise<MCPServersResponse>;

export interface McpCatalogRequestToken {
  scope: string;
  epoch: number;
}

export interface McpCatalogState {
  request: McpCatalogRequestToken;
  servers: MCPServerResponse[];
  total: number;
  isLoading: boolean;
  error: string | null;
}

function buildMCPListUrl(params: MCPListParams = {}): string {
  const searchParams = new URLSearchParams();
  if (params.skip !== undefined) searchParams.set("skip", String(params.skip));
  if (params.limit !== undefined)
    searchParams.set("limit", String(params.limit));
  if (params.q) searchParams.set("q", params.q);
  const query = searchParams.toString();
  return `${API_BASE}/${query ? `?${query}` : ""}`;
}

/** Encodes every list behavior input that can change the visible MCP catalog. */
export function buildMcpRequestScope({
  enabled,
  allAuthorizedCatalog,
  listParams,
}: {
  enabled: boolean;
  allAuthorizedCatalog: boolean;
  listParams?: MCPListParams;
}): string {
  if (!enabled) return "disabled";
  if (allAuthorizedCatalog) return "ordinary-authorized-catalog";
  return `admin-page:${JSON.stringify({
    skip: listParams?.skip ?? null,
    limit: listParams?.limit ?? null,
    q: listParams?.q ?? null,
  })}`;
}

function isMcpCatalogRequestCurrent(
  state: McpCatalogState,
  request: McpCatalogRequestToken,
): boolean {
  return (
    state.request.scope === request.scope && state.request.epoch === request.epoch
  );
}

/** Begins a list read while retaining only data from the same visible scope. */
export function beginMcpCatalogRequest(
  state: McpCatalogState,
  request: McpCatalogRequestToken,
): McpCatalogState {
  const isSameScope = state.request.scope === request.scope;
  return {
    request,
    servers: isSameScope ? state.servers : [],
    total: isSameScope ? state.total : 0,
    isLoading: true,
    error: null,
  };
}

/** Publishes a list result only when it still belongs to the active request. */
export function publishMcpCatalogSuccess(
  state: McpCatalogState,
  request: McpCatalogRequestToken,
  data: MCPServersResponse,
): McpCatalogState {
  if (!isMcpCatalogRequestCurrent(state, request)) return state;
  return {
    request,
    servers: data.servers ?? [],
    total: data.total,
    isLoading: false,
    error: null,
  };
}

/** Publishes a list failure only when it still belongs to the active request. */
export function publishMcpCatalogFailure(
  state: McpCatalogState,
  request: McpCatalogRequestToken,
  allAuthorizedCatalog: boolean,
  error: string,
): McpCatalogState {
  if (!isMcpCatalogRequestCurrent(state, request)) return state;
  return {
    request,
    servers: resolveMcpServersAfterListFailure(
      state.servers,
      allAuthorizedCatalog,
    ),
    total: allAuthorizedCatalog ? 0 : state.total,
    isLoading: false,
    error,
  };
}

/** Hides prior-scope catalog data synchronously until the matching request begins. */
export function resolveVisibleMcpCatalogState(
  state: McpCatalogState,
  requestScope: string,
  enabled: boolean,
): McpCatalogState {
  if (state.request.scope === requestScope) return state;
  return {
    request: { scope: requestScope, epoch: state.request.epoch },
    servers: [],
    total: 0,
    isLoading: enabled,
    error: null,
  };
}

/** Clear stale entries when a complete authorized catalog read cannot finish. */
export function resolveMcpServersAfterListFailure(
  current: MCPServerResponse[],
  allAuthorizedCatalog: boolean,
): MCPServerResponse[] {
  return allAuthorizedCatalog ? [] : current;
}

/** Load every authorized MCP server without exposing a partial page. */
export async function collectAllAuthorizedMcpServers(
  listPage: McpPageLoader,
): Promise<MCPServersResponse> {
  const serversByName = new Map<string, MCPServerResponse>();
  let expectedTotal: number | null = null;
  let skip = 0;

  for (
    let pageCount = 0;
    pageCount < AUTHORIZED_MCP_MAX_PAGES;
    pageCount += 1
  ) {
    const page = await listPage({
      skip,
      limit: AUTHORIZED_MCP_PAGE_LIMIT,
    });
    if (page.skip !== skip) {
      throw new Error("authorized_mcp_catalog_offset_mismatch");
    }
    if (
      !Number.isInteger(page.total) ||
      page.total < 0 ||
      page.limit !== AUTHORIZED_MCP_PAGE_LIMIT ||
      page.servers.length > AUTHORIZED_MCP_PAGE_LIMIT
    ) {
      throw new Error("authorized_mcp_catalog_invalid_page");
    }
    if (expectedTotal === null) {
      expectedTotal = page.total;
    } else if (page.total !== expectedTotal) {
      throw new Error("authorized_mcp_catalog_total_mismatch");
    }

    const nextSkip = page.skip + page.servers.length;
    if (nextSkip > expectedTotal) {
      throw new Error("authorized_mcp_catalog_invalid_progress");
    }
    if (page.servers.length === 0) {
      if (expectedTotal === 0) {
        return {
          servers: [],
          total: 0,
          skip: 0,
          limit: AUTHORIZED_MCP_PAGE_LIMIT,
        };
      }
      throw new Error("authorized_mcp_catalog_incomplete");
    }

    const priorUniqueCount = serversByName.size;
    for (const server of page.servers) {
      if (!server.name.trim()) {
        throw new Error("authorized_mcp_catalog_invalid_server");
      }
      serversByName.set(server.name, server);
    }
    if (serversByName.size === priorUniqueCount) {
      throw new Error("authorized_mcp_catalog_no_progress");
    }
    if (serversByName.size === expectedTotal) {
      if (nextSkip !== expectedTotal) {
        throw new Error("authorized_mcp_catalog_incomplete");
      }
      const servers = Array.from(serversByName.values());
      return {
        servers,
        total: servers.length,
        skip: 0,
        limit: AUTHORIZED_MCP_PAGE_LIMIT,
      };
    }
    if (nextSkip >= expectedTotal) {
      throw new Error("authorized_mcp_catalog_incomplete");
    }
    skip = nextSkip;
  }

  throw new Error("authorized_mcp_catalog_page_limit");
}

export function useMCP(options?: {
  listParams?: MCPListParams;
  enabled?: boolean;
  allAuthorizedCatalog?: boolean;
}) {
  const enabled = options?.enabled !== false;
  const listParams = options?.listParams;
  const allAuthorizedCatalog = options?.allAuthorizedCatalog === true;
  const requestScope = buildMcpRequestScope({
    enabled,
    allAuthorizedCatalog,
    listParams,
  });
  const activeRequestScopeRef = useRef(requestScope);
  const requestEpochRef = useRef(0);
  if (activeRequestScopeRef.current !== requestScope) {
    activeRequestScopeRef.current = requestScope;
    requestEpochRef.current += 1;
  }
  const [catalogState, setCatalogState] = useState<McpCatalogState>(() => ({
    request: { scope: requestScope, epoch: 0 },
    servers: [],
    total: 0,
    isLoading: false,
    error: null,
  }));
  const [operationIsLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const beginCatalogRequest = useCallback((): McpCatalogRequestToken => {
    const request = {
      scope: activeRequestScopeRef.current,
      epoch: requestEpochRef.current + 1,
    };
    requestEpochRef.current = request.epoch;
    setCatalogState((current) => beginMcpCatalogRequest(current, request));
    return request;
  }, []);
  const isActiveCatalogRequest = useCallback((request: McpCatalogRequestToken) => {
    return (
      activeRequestScopeRef.current === request.scope &&
      requestEpochRef.current === request.epoch
    );
  }, []);
  const visibleCatalogState = resolveVisibleMcpCatalogState(
    catalogState,
    requestScope,
    enabled,
  );
  const catalogScopeIsCurrent = catalogState.request.scope === requestScope;
  const servers = visibleCatalogState.servers;
  const total = visibleCatalogState.total;
  const isLoading =
    visibleCatalogState.isLoading ||
    (catalogScopeIsCurrent && operationIsLoading);
  const visibleError =
    visibleCatalogState.error ?? (catalogScopeIsCurrent ? error : null);

  // Fetch all MCP servers
  const fetchServers = useCallback(
    async (params?: MCPListParams) => {
      if (!enabled) return;
      const request = beginCatalogRequest();
      setError(null);
      try {
        const data = allAuthorizedCatalog
          ? await collectAllAuthorizedMcpServers((pageParams) =>
              authFetch<MCPServersResponse>(buildMCPListUrl(pageParams)),
            )
          : await authFetch<MCPServersResponse>(
              buildMCPListUrl(params ?? listParams ?? {}),
            );
        if (!isActiveCatalogRequest(request)) return;
        setCatalogState((current) =>
          publishMcpCatalogSuccess(current, request, data),
        );
      } catch (err) {
        if (!isActiveCatalogRequest(request)) return;
        const message =
          err instanceof Error ? err.message : "Failed to fetch MCP servers";
        setCatalogState((current) =>
          publishMcpCatalogFailure(
            current,
            request,
            allAuthorizedCatalog,
            message,
          ),
        );
      }
    },
    [
      allAuthorizedCatalog,
      beginCatalogRequest,
      enabled,
      isActiveCatalogRequest,
      listParams,
    ],
  );

  // Get single server
  const getServer = useCallback(
    async (name: string): Promise<MCPServerResponse | null> => {
      if (!enabled) return null;
      try {
        return await authFetch<MCPServerResponse>(`${API_BASE}/${encodeURIComponent(name)}`);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to fetch MCP server",
        );
        return null;
      }
    },
    [enabled],
  );

  // Create MCP server (auto-selects admin API for system servers)
  const createServer = useCallback(
    async (
      server: MCPServerCreate,
      isSystem: boolean = false,
    ): Promise<MCPServerResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const baseUrl = isSystem ? "/api/admin/mcp" : API_BASE;
        const data: MCPServerResponse = await authFetch(`${baseUrl}/`, {
          method: "POST",
          body: JSON.stringify(server),
        });
        await fetchServers();
        return data;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to create MCP server",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Update MCP server (auto-selects admin API for system servers)
  const updateServer = useCallback(
    async (
      name: string,
      updates: MCPServerUpdate,
      isSystem: boolean = false,
    ): Promise<MCPServerResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const baseUrl = isSystem ? "/api/admin/mcp" : API_BASE;
        const data: MCPServerResponse = await authFetch(
          `${baseUrl}/${encodeURIComponent(name)}`,
          {
            method: "PUT",
            body: JSON.stringify(updates),
          },
        );
        await fetchServers();
        return data;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to update MCP server",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Delete MCP server (auto-selects admin API for system servers)
  const deleteServer = useCallback(
    async (name: string, isSystem: boolean = false): Promise<boolean> => {
      if (!enabled) return false;
      setIsLoading(true);
      setError(null);
      try {
        const baseUrl = isSystem ? "/api/admin/mcp" : API_BASE;
        await authFetch(`${baseUrl}/${encodeURIComponent(name)}`, {
          method: "DELETE",
        });
        await fetchServers();
        return true;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to delete MCP server",
        );
        return false;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Toggle server enabled status
  const toggleServer = useCallback(
    async (name: string): Promise<MCPServerResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const data: MCPServerToggleResponse = await authFetch(
          `${API_BASE}/${encodeURIComponent(name)}/toggle`,
          {
            method: "PATCH",
          },
        );
        await fetchServers();
        return data.server;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to toggle MCP server",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Import servers from JSON
  const importServers = useCallback(
    async (request: MCPImportRequest): Promise<MCPImportResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const data: MCPImportResponse = await authFetch(`${API_BASE}/import`, {
          method: "POST",
          body: JSON.stringify(request),
        });
        await fetchServers();
        return data;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to import MCP servers",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Export servers to JSON
  const exportServers = useCallback(
    async (): Promise<MCPExportResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        return await authFetch<MCPExportResponse>(`${API_BASE}/export`);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to export MCP servers",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled],
  );

  // Promote user server to system server (admin only)
  const promoteServer = useCallback(
    async (
      name: string,
      ownerUserId: string,
    ): Promise<MCPServerMoveResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const data: MCPServerMoveResponse = await authFetch(
          `/api/admin/mcp/${encodeURIComponent(name)}/promote`,
          {
            method: "POST",
            body: JSON.stringify({ target_user_id: ownerUserId }),
          },
        );
        await fetchServers();
        return data;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to promote MCP server",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Demote system server to user server (admin only)
  const demoteServer = useCallback(
    async (
      name: string,
      targetUserId: string,
    ): Promise<MCPServerMoveResponse | null> => {
      if (!enabled) return null;
      setIsLoading(true);
      setError(null);
      try {
        const data: MCPServerMoveResponse = await authFetch(
          `/api/admin/mcp/${encodeURIComponent(name)}/demote`,
          {
            method: "POST",
            body: JSON.stringify({ target_user_id: targetUserId }),
          },
        );
        await fetchServers();
        return data;
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to demote MCP server",
        );
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    [enabled, fetchServers],
  );

  // Initial load
  useEffect(() => {
    if (!enabled) return;
    fetchServers();
  }, [enabled, fetchServers]);

  return {
    servers,
    total,
    isLoading,
    error: visibleError,
    fetchServers,
    getServer,
    createServer,
    updateServer,
    deleteServer,
    toggleServer,
    importServers,
    exportServers,
    promoteServer,
    demoteServer,
    clearError: () => {
      setError(null);
      setCatalogState((current) =>
        current.request.scope === activeRequestScopeRef.current
          ? { ...current, error: null }
          : current,
      );
    },
  };
}
