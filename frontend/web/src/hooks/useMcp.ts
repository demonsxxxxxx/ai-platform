import { useState, useCallback, useEffect, useRef } from "react";
import { ApiRequestError, authFetch } from "../services/api/fetch";
import { isPermissionError } from "../components/governance/frontendGovernanceState";
import type {
  MCPServerResponse,
  MCPServersResponse,
  MCPServerCreate,
  MCPServerUpdate,
  MCPServerToggleResponse,
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
  permissionDenied?: boolean;
  errorSource?: "catalog" | "operation";
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

/** Allocates the next request only when the callback still owns the active scope. */
export function allocateMcpCatalogRequest(
  activeScope: string,
  callbackScope: string,
  currentEpoch: number,
): McpCatalogRequestToken | null {
  if (activeScope !== callbackScope) return null;
  return { scope: callbackScope, epoch: currentEpoch + 1 };
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
  permissionDenied = false,
  errorSource: "catalog" | "operation" = "catalog",
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
    permissionDenied,
    errorSource,
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

function catalogAllowsOperations(state: McpCatalogState): boolean {
  return !state.isLoading && (!state.error ||
    (state.errorSource === "operation" && !state.permissionDenied));
}

/** One request owner retains failures and fences every read/detail/mutation. */
export function useMCP(options?: {
  listParams?: MCPListParams;
  enabled?: boolean;
  allAuthorizedCatalog?: boolean;
  identityScope?: string;
  canManageServers?: boolean;
  paused?: boolean;
}) {
  const enabled = options?.enabled !== false;
  const listParams = options?.listParams;
  const allAuthorizedCatalog = options?.allAuthorizedCatalog === true;
  const identityScope = options?.identityScope ?? "";
  const canManageServers = options?.canManageServers === true;
  const mountedRef = useRef(false);
  const paused = options?.paused === true;
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const requestScope = JSON.stringify([identityScope, canManageServers, buildMcpRequestScope({
    enabled,
    allAuthorizedCatalog,
    listParams,
  })]);
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
    isLoading: enabled,
    error: null,
  }));
  // This ref is the synchronous admission view of the same state, not a second
  // permission latch. It blocks stale callbacks before React commits a render.
  const catalogStateRef = useRef(catalogState);
  const updateCatalog = useCallback((update: (state: McpCatalogState) => McpCatalogState) => {
    const next = update(catalogStateRef.current);
    catalogStateRef.current = next;
    setCatalogState(next);
  }, []);
  const [operationRequest, setOperationRequest] = useState<McpCatalogRequestToken | null>(null);
  const operationRequestRef = useRef<McpCatalogRequestToken | null>(null);
  const beginCatalogRequest = useCallback(
    (callbackScope: string): McpCatalogRequestToken | null => {
      const request = allocateMcpCatalogRequest(
        activeRequestScopeRef.current,
        callbackScope,
        requestEpochRef.current,
      );
      if (!request) return null;
      requestEpochRef.current = request.epoch;
      updateCatalog((current) => beginMcpCatalogRequest(current, request));
      return request;
    },
    [updateCatalog],
  );
  const isActiveCatalogRequest = useCallback((request: McpCatalogRequestToken) => {
    return (
      mountedRef.current && !pausedRef.current &&
      activeRequestScopeRef.current === request.scope &&
      requestEpochRef.current === request.epoch
    );
  }, []);
  const publishFailure = useCallback((request: McpCatalogRequestToken, err: unknown, fallback: string, source: "catalog" | "operation" = "catalog") => {
    updateCatalog((current) => publishMcpCatalogFailure(
      current,
      request,
      allAuthorizedCatalog,
      err instanceof Error ? err.message : fallback,
      (err instanceof ApiRequestError && err.status === 403) ||
        (err instanceof Error && isPermissionError(err.message)),
      source,
    ));
  }, [allAuthorizedCatalog, updateCatalog]);
  const visibleCatalogState = resolveVisibleMcpCatalogState(catalogState, requestScope, enabled);
  const isAvailable = enabled && !paused && isActiveCatalogRequest(catalogState.request) &&
    catalogState.request.epoch > 0 && catalogAllowsOperations(catalogState);
  const isLoading = visibleCatalogState.isLoading ||
    (operationRequest !== null && isActiveCatalogRequest(operationRequest));

  // Retry is a read only. Neither errors nor mutation results trigger retries.
  const fetchServers = useCallback(async (params?: MCPListParams): Promise<boolean> => {
    if (!mountedRef.current || pausedRef.current || !enabled) return false;
    const current = catalogStateRef.current;
    // A directory GET cannot prove write authority after a denial. The caller
    // must refresh the authoritative auth projection, which replaces scope.
    if (current.request.scope === requestScope && current.permissionDenied) return false;
    const requestParams = params ?? listParams;
    const callbackScope = JSON.stringify([identityScope, canManageServers, buildMcpRequestScope({
      enabled, allAuthorizedCatalog, listParams: requestParams,
    })]);
    const request = beginCatalogRequest(callbackScope);
    if (!request) return false;
    try {
      const data = allAuthorizedCatalog
        ? await collectAllAuthorizedMcpServers((pageParams) => {
            if (!isActiveCatalogRequest(request)) throw new Error("obsolete_mcp_catalog_request");
            return authFetch<MCPServersResponse>(buildMCPListUrl(pageParams));
          })
        : await authFetch<MCPServersResponse>(buildMCPListUrl(requestParams ?? {}));
      if (!isActiveCatalogRequest(request)) return false;
      updateCatalog((current) => publishMcpCatalogSuccess(current, request, data));
      return true;
    } catch (err) {
      if (!isActiveCatalogRequest(request)) return false;
      publishFailure(request, err, "Failed to fetch MCP servers");
      return false;
    }
  }, [enabled, requestScope, listParams, identityScope, canManageServers, allAuthorizedCatalog, beginCatalogRequest, isActiveCatalogRequest, updateCatalog, publishFailure]);

  // Details and mutations share the catalog's authorization and lifetime. A
  // new read, identity change, or failure fences every older completion.
  const requestServer = useCallback(async <T,>(
    load: () => Promise<T>, fallback: string, mutation: boolean,
  ): Promise<T | null> => {
    const current = catalogStateRef.current;
    if (!mountedRef.current || pausedRef.current || !enabled || !canManageServers || activeRequestScopeRef.current !== requestScope ||
      !isActiveCatalogRequest(current.request) || current.request.epoch === 0 ||
      !catalogAllowsOperations(current) ||
      (operationRequestRef.current && isActiveCatalogRequest(operationRequestRef.current))) return null;
    const request = current.request;
    if (current.errorSource === "operation") {
      updateCatalog((state) => ({ ...state, error: null, errorSource: undefined }));
    }
    if (mutation) {
      operationRequestRef.current = request;
      setOperationRequest(request);
    }
    try {
      const result = await load();
      if (!isActiveCatalogRequest(request)) return null;
      if (mutation && !await fetchServers()) return null;
      return result;
    } catch (err) {
      if (!isActiveCatalogRequest(request)) return null;
      // Advance the same request owner so concurrent old details cannot reopen
      // an editor after the backend has rejected this scope.
      const failureRequest = beginCatalogRequest(requestScope);
      if (failureRequest) publishFailure(failureRequest, err, fallback, "operation");
      return null;
    } finally {
      if (mutation && operationRequestRef.current === request) {
        operationRequestRef.current = null;
        setOperationRequest(null);
      }
    }
  }, [enabled, canManageServers, requestScope, isActiveCatalogRequest, fetchServers, beginCatalogRequest, publishFailure, updateCatalog]);

  const getServer = useCallback((name: string) => requestServer(
    () => authFetch<MCPServerResponse>(`${API_BASE}/${encodeURIComponent(name)}`),
    "Failed to fetch MCP server", false,
  ), [requestServer]);
  const createServer = useCallback((server: MCPServerCreate, isSystem = false) => requestServer(
    () => authFetch<MCPServerResponse>(`${isSystem ? "/api/admin/mcp" : API_BASE}/`, {
      method: "POST", body: JSON.stringify(server),
    }), "Failed to create MCP server", true,
  ), [requestServer]);
  const updateServer = useCallback((name: string, updates: MCPServerUpdate, isSystem = false) => requestServer(
    () => authFetch<MCPServerResponse>(`${isSystem ? "/api/admin/mcp" : API_BASE}/${encodeURIComponent(name)}`, {
      method: "PUT", body: JSON.stringify(updates),
    }), "Failed to update MCP server", true,
  ), [requestServer]);
  const deleteServer = useCallback(async (name: string, isSystem = false): Promise<boolean> => {
    const result = await requestServer(async () => {
      await authFetch(`${isSystem ? "/api/admin/mcp" : API_BASE}/${encodeURIComponent(name)}`, { method: "DELETE" });
      return true;
    }, "Failed to delete MCP server", true);
    return result === true;
  }, [requestServer]);
  const toggleServer = useCallback(async (name: string): Promise<MCPServerResponse | null> => {
    const result = await requestServer(
      () => authFetch<MCPServerToggleResponse>(`${API_BASE}/${encodeURIComponent(name)}/toggle`, { method: "PATCH" }),
      "Failed to toggle MCP server", true,
    );
    return result?.server ?? null;
  }, [requestServer]);

  useEffect(() => {
    mountedRef.current = true;
    void fetchServers();
    return () => {
      mountedRef.current = false;
      requestEpochRef.current += 1;
    };
  }, [fetchServers, paused]);

  return {
    servers: visibleCatalogState.servers,
    total: visibleCatalogState.total,
    isLoading,
    isAvailable,
    permissionDenied: visibleCatalogState.permissionDenied === true,
    error: visibleCatalogState.error,
    catalogError: visibleCatalogState.errorSource === "operation" && !visibleCatalogState.permissionDenied
      ? null : visibleCatalogState.error,
    fetchServers,
    getServer,
    createServer,
    updateServer,
    deleteServer,
    toggleServer,
  };
}
