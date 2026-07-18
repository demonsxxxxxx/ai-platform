/**
 * 认证上下文和 Hook
 * 提供全局认证状态管理
 */

import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useRef,
  type ReactNode,
} from "react";
import {
  authApi,
  buildOAuthLoginUrl,
  getAccessToken,
  getRedirectPath,
  clearRedirectPath,
} from "../services/api";
import {
  clearTokens,
  migrateLegacyBearerStorage,
  setTokens,
} from "../services/api/token";
import { ApiRequestError } from "../services/api/fetch";
import { clearAuthScopedCaches } from "../services/api/authCacheInvalidation";
import { classifyBrowserAuthStorageEvent } from "./browserAuthStorage";
import {
  ensureBrowserAuthContext,
  ensureBrowserAuthContextBeforeLogin,
  publishBrowserAuthIncarnationChange,
} from "./browserAuthCoordinator";
import { DEFAULT_THINKING_LEVEL_STORAGE_KEY } from "../components/layout/AppContent/useAgentOptions";
import {
  hasAllEffectivePermissions,
  hasAnyEffectivePermission,
  hasEffectivePermission,
} from "../components/governance/permissionProjection";
import { THEME_STORAGE_KEY } from "../utils/themeDom";
import { Permission } from "../types";
import type { User, UserCreate, LoginRequest, AuthState } from "../types";

export const SIDEBAR_COLLAPSED_STORAGE_KEY = "ai-platform-sidebar-collapsed";

/** Apply user metadata preferences from backend */
function applyUserMetadata(metadata?: {
  language?: string;
  theme?: string;
  defaultThinkingLevel?: string;
  sidebarCollapsed?: string;
}) {
  if (!metadata) return;

  if (metadata.theme) {
    localStorage.setItem(THEME_STORAGE_KEY, metadata.theme);
    // Notify ThemeContext to update React state + DOM in sync
    window.dispatchEvent(
      new CustomEvent("theme:external-change", { detail: metadata.theme }),
    );
  }

  if (metadata.defaultThinkingLevel) {
    localStorage.setItem(
      DEFAULT_THINKING_LEVEL_STORAGE_KEY,
      metadata.defaultThinkingLevel,
    );
    window.dispatchEvent(
      new CustomEvent("thinking-preference-updated", {
        detail: metadata.defaultThinkingLevel,
      }),
    );
  }

  if (metadata.sidebarCollapsed !== undefined) {
    localStorage.setItem(
      SIDEBAR_COLLAPSED_STORAGE_KEY,
      metadata.sidebarCollapsed,
    );
    window.dispatchEvent(
      new CustomEvent("sidebar-collapsed-changed", {
        detail: metadata.sidebarCollapsed === "true",
      }),
    );
  }

}

/** Explicit completion contract for caller-visible identity operations. */
export type AuthOperationOutcome<T = undefined> =
  | { status: "completed"; value: T }
  | { status: "cancelled" }
  | { status: "failed"; error?: unknown };

function completedAuthOperation<T>(value: T): AuthOperationOutcome<T> {
  return { status: "completed", value };
}

function cancelledAuthOperation<T = undefined>(): AuthOperationOutcome<T> {
  return { status: "cancelled" };
}

function failedAuthOperation<T = undefined>(error?: unknown): AuthOperationOutcome<T> {
  return error === undefined ? { status: "failed" } : { status: "failed", error };
}

function isUnauthenticatedError(error: unknown): boolean {
  return (
    error instanceof ApiRequestError &&
    (error.status === 401 || error.status === 403)
  );
}

function isAuthContextStale(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === "auth_context_stale";
}

// 认证上下文类型
interface AuthContextType extends AuthState {
  login: (
    credentials: LoginRequest,
    turnstileToken?: string,
  ) => Promise<AuthOperationOutcome<string | null>>;
  register: (
    userData: UserCreate,
    turnstileToken?: string,
  ) => Promise<{ requiresVerification: boolean; email: string }>;
  loginWithOAuth: (provider: string) => Promise<void>;
  handleOAuthCallback: (
    provider: string,
    code: string,
    state: string,
  ) => Promise<AuthOperationOutcome>;
  logout: () => Promise<boolean>;
  refreshUser: () => Promise<AuthOperationOutcome>;
  hasPermission: (permission: Permission) => boolean;
  hasAnyPermission: (permissions: Permission[]) => boolean;
  hasAllPermissions: (permissions: Permission[]) => boolean;
}

// 创建认证上下文
const AuthContext = createContext<AuthContextType | null>(null);

interface AuthOperationOwner {
  generation: number;
  abortController: AbortController;
  expectedMarker: string | null;
}

// Auth Provider 组件
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(getAccessToken());
  const [isLoading, setIsLoading] = useState(true);
  // 存储从 API 获取的动态权限
  const [dynamicPermissions, setDynamicPermissions] = useState<Permission[]>(
    [],
  );
  const mountedRef = useRef(false);
  const authOperationGenerationRef = useRef(0);
  const authOperationAbortControllerRef = useRef<AbortController | null>(null);

  // 权限列表：从 API 动态获取
  const permissions = dynamicPermissions;

  const invalidateAuthOperation = useCallback(() => {
    authOperationGenerationRef.current += 1;
    authOperationAbortControllerRef.current?.abort();
    authOperationAbortControllerRef.current = null;
  }, []);

  const beginAuthOperation = useCallback((
    expectedMarker: string | null = getAccessToken(),
  ): AuthOperationOwner => {
    invalidateAuthOperation();
    const abortController = new AbortController();
    authOperationAbortControllerRef.current = abortController;
    return {
      generation: authOperationGenerationRef.current,
      abortController,
      expectedMarker,
    };
  }, [invalidateAuthOperation]);

  const isOwnedAuthOperation = useCallback((owner: AuthOperationOwner) => (
    mountedRef.current &&
    authOperationGenerationRef.current === owner.generation &&
    authOperationAbortControllerRef.current === owner.abortController &&
    !owner.abortController.signal.aborted
  ), []);

  const isCurrentAuthOperation = useCallback((owner: AuthOperationOwner) => (
    isOwnedAuthOperation(owner) && getAccessToken() === owner.expectedMarker
  ), [isOwnedAuthOperation]);

  const applyAuthenticatedUser = useCallback((
    currentUser: User,
    owner: AuthOperationOwner,
  ): boolean => {
    if (!isCurrentAuthOperation(owner)) return false;
    setToken(owner.expectedMarker);
    setUser(currentUser);
    applyUserMetadata(currentUser.metadata);
    if (currentUser.permissions) {
      setDynamicPermissions(
        currentUser.permissions.filter((p): p is Permission =>
          Object.values(Permission).includes(p as Permission),
        ),
      );
    } else {
      setDynamicPermissions([]);
    }
    // This is the production same-tab ownership seam for login and principal
    // refreshes. Run-control listeners fence old work synchronously here;
    // `storage` is intentionally reserved for cross-tab changes.
    publishBrowserAuthIncarnationChange();
    return true;
  }, [isCurrentAuthOperation]);

  const clearAuthPresentation = useCallback((
    owner: AuthOperationOwner,
    clearMarker: boolean,
  ): boolean => {
    if (!isCurrentAuthOperation(owner)) return false;
    if (clearMarker) {
      clearTokens();
      owner.expectedMarker = null;
    }
    clearAuthScopedCaches();
    setToken(null);
    setUser(null);
    setDynamicPermissions([]);
    // Logout and cross-tab replacement clear the visible principal through
    // this path. Publish even when the marker was already replaced elsewhere.
    publishBrowserAuthIncarnationChange();
    return true;
  }, [isCurrentAuthOperation]);

  const applyLoggedOut = useCallback((owner: AuthOperationOwner): boolean => {
    return clearAuthPresentation(owner, true);
  }, [clearAuthPresentation]);

  const establishLocalSession = useCallback((
    owner: AuthOperationOwner,
    accessToken = "cookie-session",
    refreshToken?: string,
  ): boolean => {
    if (!isCurrentAuthOperation(owner)) return false;
    clearAuthScopedCaches();
    setTokens(accessToken, refreshToken);
    if (!isOwnedAuthOperation(owner)) return false;
    const marker = getAccessToken();
    if (!marker) return false;
    owner.expectedMarker = marker;
    window.dispatchEvent(new CustomEvent("auth:login"));
    return isCurrentAuthOperation(owner);
  }, [isCurrentAuthOperation, isOwnedAuthOperation]);

  const rollbackOwnedSession = useCallback(async (
    owner: AuthOperationOwner,
  ): Promise<boolean> => {
    if (!isCurrentAuthOperation(owner)) return false;
    try {
      await authApi.logout(owner.abortController.signal);
    } catch {
      // Preserve the hydration failure. Local convergence still happens in
      // finally while this exact marker/generation remains authoritative.
    } finally {
      if (isCurrentAuthOperation(owner)) applyLoggedOut(owner);
    }
    return isCurrentAuthOperation(owner);
  }, [applyLoggedOut, isCurrentAuthOperation]);

  const getCurrentUserWithOneStaleRepair = useCallback(async (
    owner: AuthOperationOwner,
  ): Promise<User> => {
    try {
      return await authApi.getCurrentUser({ signal: owner.abortController.signal });
    } catch (error) {
      if (!isAuthContextStale(error) || !isCurrentAuthOperation(owner)) {
        throw error;
      }
      // A stale HttpOnly cookie can only be repaired through the persisted
      // current coordinator state. This retries one idempotent principal GET;
      // callers never replay login/logout/OAuth or another mutation POST.
      await ensureBrowserAuthContext(owner.abortController.signal, {
        forceBootstrap: true,
      });
      if (!isCurrentAuthOperation(owner)) {
        throw owner.abortController.signal.reason
          ?? new DOMException("Auth operation superseded", "AbortError");
      }
      return authApi.getCurrentUser({ signal: owner.abortController.signal });
    }
  }, [isCurrentAuthOperation]);

  const hydrateOwnedUser = useCallback(async (
    owner: AuthOperationOwner,
    failClosedOnAnyError: boolean,
  ): Promise<AuthOperationOutcome> => {
    try {
      const currentUser = await getCurrentUserWithOneStaleRepair(owner);
      if (!applyAuthenticatedUser(currentUser, owner)) {
        return cancelledAuthOperation();
      }
      return completedAuthOperation(undefined);
    } catch (error) {
      if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
      if (failClosedOnAnyError || isUnauthenticatedError(error)) {
        applyLoggedOut(owner);
        return failedAuthOperation();
      }
      console.error("[useAuth] Failed to refresh the authenticated principal");
      return failedAuthOperation();
    } finally {
      if (isCurrentAuthOperation(owner)) setIsLoading(false);
    }
  }, [
    applyAuthenticatedUser,
    applyLoggedOut,
    getCurrentUserWithOneStaleRepair,
    isCurrentAuthOperation,
  ]);

  const refreshUser = useCallback(async (): Promise<AuthOperationOutcome> => {
    const owner = beginAuthOperation();
    if (isCurrentAuthOperation(owner)) setIsLoading(true);
    try {
      await ensureBrowserAuthContext(owner.abortController.signal);
      if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
      return await hydrateOwnedUser(owner, false);
    } catch (error) {
      if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
      applyLoggedOut(owner);
      return failedAuthOperation(error);
    }
  }, [
    applyLoggedOut,
    beginAuthOperation,
    hydrateOwnedUser,
    isCurrentAuthOperation,
  ]);

  // 初始化：检查现有 token 并获取用户信息
  useEffect(() => {
    mountedRef.current = true;
    const owner = beginAuthOperation();
    if (isCurrentAuthOperation(owner)) {
      migrateLegacyBearerStorage();
    }
    const initAuth = async () => {
      const hadSessionMarker = !!owner.expectedMarker;

      try {
        await ensureBrowserAuthContext(owner.abortController.signal);
        if (!isCurrentAuthOperation(owner)) return;
        const currentUser = await getCurrentUserWithOneStaleRepair(owner);
        if (!isCurrentAuthOperation(owner)) return;
        if (!hadSessionMarker && !establishLocalSession(owner)) return;
        applyAuthenticatedUser(currentUser, owner);
      } catch {
        if (!isCurrentAuthOperation(owner)) return;
        applyLoggedOut(owner);
      } finally {
        if (isCurrentAuthOperation(owner)) setIsLoading(false);
      }
    };

    void initAuth();
    return () => {
      mountedRef.current = false;
      invalidateAuthOperation();
    };
  }, [
    applyAuthenticatedUser,
    applyLoggedOut,
    beginAuthOperation,
    establishLocalSession,
    getCurrentUserWithOneStaleRepair,
    invalidateAuthOperation,
    isCurrentAuthOperation,
  ]);

  // 监听登出事件
  useEffect(() => {
    const handleLogout = () => {
      const owner = beginAuthOperation();
      applyLoggedOut(owner);
      if (isCurrentAuthOperation(owner)) setIsLoading(false);
    };

    const handleStorage = (event: StorageEvent) => {
      const change = classifyBrowserAuthStorageEvent(event);
      if (!change) return;
      if (change.type === "logout") {
        const owner = beginAuthOperation(null);
        applyLoggedOut(owner);
        if (isCurrentAuthOperation(owner)) setIsLoading(false);
        return;
      }

      const owner = beginAuthOperation(change.marker);
      if (!isCurrentAuthOperation(owner)) return;
      // A marker value change is an identity replacement, not a refresh of A.
      // Clear A before asking the backend to project B.
      clearAuthPresentation(owner, false);
      setIsLoading(true);
      void hydrateOwnedUser(owner, true);
    };

    window.addEventListener("auth:logout", handleLogout);
    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("auth:logout", handleLogout);
      window.removeEventListener("storage", handleStorage);
    };
  }, [
    applyLoggedOut,
    beginAuthOperation,
    clearAuthPresentation,
    hydrateOwnedUser,
    isCurrentAuthOperation,
  ]);

  // 登录
  const login = useCallback(
    async (
      credentials: LoginRequest,
      turnstileToken?: string,
    ): Promise<AuthOperationOutcome<string | null>> => {
      const owner = beginAuthOperation();
      if (isCurrentAuthOperation(owner)) setIsLoading(true);
      let sessionEstablished = false;
      try {
        await ensureBrowserAuthContextBeforeLogin(owner.abortController.signal);
        if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
        await authApi.login(
          credentials,
          turnstileToken,
          owner.abortController.signal,
        );
        if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
        sessionEstablished = true;
        if (!establishLocalSession(owner)) return cancelledAuthOperation();
        const currentUser = await getCurrentUserWithOneStaleRepair(owner);
        if (!applyAuthenticatedUser(currentUser, owner)) {
          return cancelledAuthOperation();
        }

        // 登录成功后，跳转到之前的页面
        const redirectPath = getRedirectPath();
        if (redirectPath) {
          clearRedirectPath();
        }
        return completedAuthOperation(redirectPath ?? null);
      } catch (error) {
        if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
        if (sessionEstablished) {
          const converged = await rollbackOwnedSession(owner);
          if (!converged) return cancelledAuthOperation();
        }
        throw error;
      } finally {
        if (isCurrentAuthOperation(owner)) setIsLoading(false);
      }
    },
    [
      applyAuthenticatedUser,
      beginAuthOperation,
      establishLocalSession,
      getCurrentUserWithOneStaleRepair,
      isCurrentAuthOperation,
      rollbackOwnedSession,
    ],
  );

  // 注册
  const register = useCallback(
    async (
      userData: UserCreate,
      turnstileToken?: string,
    ): Promise<{ requiresVerification: boolean; email: string }> => {
      setIsLoading(true);
      try {
        const response = await authApi.register(userData, turnstileToken);
        return {
          requiresVerification: response.requires_verification,
          email: userData.email,
        };
      } finally {
        setIsLoading(false);
      }
    },
    [],
  );

  // OAuth 登录由服务端 state 绑定同一个 browser auth context。
  const loginWithOAuth = useCallback(async (provider: string) => {
    const owner = beginAuthOperation();
    if (isCurrentAuthOperation(owner)) setIsLoading(true);
    try {
      await ensureBrowserAuthContext(owner.abortController.signal);
      if (!isCurrentAuthOperation(owner)) return;
      const { state } = await authApi.beginOAuth(
        provider,
        owner.abortController.signal,
      );
      if (!isCurrentAuthOperation(owner)) return;
      window.location.href = buildOAuthLoginUrl(provider, state);
    } finally {
      if (isCurrentAuthOperation(owner)) setIsLoading(false);
    }
  }, [beginAuthOperation, isCurrentAuthOperation]);

  // 处理 OAuth 回调
  const handleOAuthCallback = useCallback(
    async (
      provider: string,
      code: string,
      state: string,
    ): Promise<AuthOperationOutcome> => {
      const owner = beginAuthOperation();
      if (isCurrentAuthOperation(owner)) setIsLoading(true);
      let sessionEstablished = false;
      try {
        await ensureBrowserAuthContext(owner.abortController.signal);
        if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
        await authApi.handleOAuthCallback(
          provider,
          code,
          state,
          owner.abortController.signal,
        );
        if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
        sessionEstablished = true;
        if (!establishLocalSession(owner)) return cancelledAuthOperation();
        const currentUser = await getCurrentUserWithOneStaleRepair(owner);
        if (!applyAuthenticatedUser(currentUser, owner)) {
          return cancelledAuthOperation();
        }
        return completedAuthOperation(undefined);
      } catch (error) {
        if (!isCurrentAuthOperation(owner)) return cancelledAuthOperation();
        if (sessionEstablished) {
          const converged = await rollbackOwnedSession(owner);
          if (!converged) return cancelledAuthOperation();
        }
        throw error;
      } finally {
        if (isCurrentAuthOperation(owner)) setIsLoading(false);
      }
    },
    [
      applyAuthenticatedUser,
      beginAuthOperation,
      establishLocalSession,
      getCurrentUserWithOneStaleRepair,
      isCurrentAuthOperation,
      rollbackOwnedSession,
    ],
  );

  // 登出
  const logout = useCallback(async () => {
    const owner = beginAuthOperation();
    if (isCurrentAuthOperation(owner)) setIsLoading(true);
    try {
      await authApi.logout(owner.abortController.signal);
      if (!isCurrentAuthOperation(owner)) return true;
      applyLoggedOut(owner);
      return true;
    } catch {
      if (!isCurrentAuthOperation(owner)) return true;
      console.error("[useAuth] Failed to close the current session");
      return false;
    } finally {
      if (isCurrentAuthOperation(owner)) setIsLoading(false);
    }
  }, [applyLoggedOut, beginAuthOperation, isCurrentAuthOperation]);

  // 检查是否拥有某个权限
  const hasPermission = useCallback(
    (permission: Permission): boolean => {
      return hasEffectivePermission(permissions, permission);
    },
    [permissions],
  );

  // 检查是否拥有任意一个权限
  const hasAnyPermission = useCallback(
    (perms: Permission[]): boolean => {
      return hasAnyEffectivePermission(permissions, perms);
    },
    [permissions],
  );

  // 检查是否拥有所有权限
  const hasAllPermissions = useCallback(
    (perms: Permission[]): boolean => {
      return hasAllEffectivePermissions(permissions, perms);
    },
    [permissions],
  );

  const value: AuthContextType = {
    user,
    token,
    isAuthenticated: !!token && !!user,
    isLoading,
    permissions,
    login,
    register,
    loginWithOAuth,
    handleOAuthCallback,
    logout,
    refreshUser,
    hasPermission,
    hasAnyPermission,
    hasAllPermissions,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// useAuth Hook
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

// 默认导出
// eslint-disable-next-line react-refresh/only-export-components
export default useAuth;
