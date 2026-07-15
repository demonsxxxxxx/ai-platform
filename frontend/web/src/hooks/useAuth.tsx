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
  isAuthenticated,
} from "../services/api";
import { setTokens } from "../services/api/token";
import { handleBrowserAuthStorageEvent } from "./browserAuthStorage";
import { DEFAULT_THINKING_LEVEL_STORAGE_KEY } from "../components/layout/AppContent/useAgentOptions";
import {
  hasAllEffectivePermissions,
  hasAnyEffectivePermission,
  hasEffectivePermission,
} from "../components/governance/permissionProjection";
import { THEME_STORAGE_KEY } from "../utils/themeDom";
import { Permission } from "../types";
import type { User, UserCreate, LoginRequest, AuthState } from "../types";
import i18n from "../i18n";

export const SIDEBAR_COLLAPSED_STORAGE_KEY = "ai-platform-sidebar-collapsed";

/** Apply user metadata preferences from backend */
function applyUserMetadata(metadata?: {
  language?: string;
  theme?: string;
  defaultThinkingLevel?: string;
  sidebarCollapsed?: string;
  defaultAgentId?: string;
}) {
  if (!metadata) return;

  if (metadata.language) {
    localStorage.setItem("language", metadata.language);
    i18n.changeLanguage(metadata.language);
  }

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

  if (metadata.defaultAgentId) {
    localStorage.setItem("defaultAgentId", metadata.defaultAgentId);
    window.dispatchEvent(
      new CustomEvent("agent-preference-updated", {
        detail: { agentId: metadata.defaultAgentId },
      }),
    );
  }
}

// 认证上下文类型
interface AuthContextType extends AuthState {
  login: (
    credentials: LoginRequest,
    turnstileToken?: string,
  ) => Promise<string | null>;
  register: (
    userData: UserCreate,
    turnstileToken?: string,
  ) => Promise<{ requiresVerification: boolean; email: string }>;
  loginWithOAuth: (provider: string) => Promise<void>;
  handleOAuthCallback: (
    provider: string,
    code: string,
    state: string,
  ) => Promise<void>;
  logout: () => Promise<boolean>;
  refreshUser: () => Promise<void>;
  hasPermission: (permission: Permission) => boolean;
  hasAnyPermission: (permissions: Permission[]) => boolean;
  hasAllPermissions: (permissions: Permission[]) => boolean;
}

// 创建认证上下文
const AuthContext = createContext<AuthContextType | null>(null);

interface AuthOperationOwner {
  generation: number;
  abortController: AbortController;
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

  const beginAuthOperation = useCallback((): AuthOperationOwner => {
    invalidateAuthOperation();
    const abortController = new AbortController();
    authOperationAbortControllerRef.current = abortController;
    return {
      generation: authOperationGenerationRef.current,
      abortController,
    };
  }, [invalidateAuthOperation]);

  const isCurrentAuthOperation = useCallback((owner: AuthOperationOwner) => (
    mountedRef.current &&
    authOperationGenerationRef.current === owner.generation &&
    authOperationAbortControllerRef.current === owner.abortController &&
    !owner.abortController.signal.aborted
  ), []);

  const applyAuthenticatedUser = useCallback((
    currentUser: User,
    owner: AuthOperationOwner,
  ): boolean => {
    if (!isCurrentAuthOperation(owner)) return false;
    setToken(getAccessToken());
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
    return true;
  }, [isCurrentAuthOperation]);

  const applyLoggedOut = useCallback((owner: AuthOperationOwner): boolean => {
    if (!isCurrentAuthOperation(owner)) return false;
    setToken(null);
    setUser(null);
    setDynamicPermissions([]);
    return true;
  }, [isCurrentAuthOperation]);

  const refreshUser = useCallback(async () => {
    if (!isAuthenticated()) return;
    const owner = beginAuthOperation();
    if (isCurrentAuthOperation(owner)) setIsLoading(true);
    try {
      const currentUser = await authApi.getCurrentUser({
        signal: owner.abortController.signal,
      });
      applyAuthenticatedUser(currentUser, owner);
    } catch (error) {
      if (!isCurrentAuthOperation(owner)) return;
      if (error instanceof Error && /Unauthorized/i.test(error.message)) {
        applyLoggedOut(owner);
        return;
      }
      console.error("Failed to refresh user info:", error);
    } finally {
      if (isCurrentAuthOperation(owner)) setIsLoading(false);
    }
  }, [applyAuthenticatedUser, applyLoggedOut, beginAuthOperation, isCurrentAuthOperation]);

  // 初始化：检查现有 token 并获取用户信息
  useEffect(() => {
    mountedRef.current = true;
    const owner = beginAuthOperation();
    const initAuth = async () => {
      const hadSessionMarker = !!getAccessToken();

      try {
        const currentUser = await authApi.getCurrentUser({
          signal: owner.abortController.signal,
        });
        if (!isCurrentAuthOperation(owner)) return;
        if (!hadSessionMarker) {
          setTokens("cookie-session");
        }
        if (applyAuthenticatedUser(currentUser, owner) && !hadSessionMarker) {
          window.dispatchEvent(new CustomEvent("auth:login"));
        }
      } catch (err) {
        if (!isCurrentAuthOperation(owner)) return;
        if (err instanceof Error && /Unauthorized/i.test(err.message)) {
          applyLoggedOut(owner);
          return;
        }
        console.warn("[useAuth] Failed to fetch current user:", err);
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
      handleBrowserAuthStorageEvent(event, refreshUser);
    };

    window.addEventListener("auth:logout", handleLogout);
    window.addEventListener("storage", handleStorage);
    return () => {
      window.removeEventListener("auth:logout", handleLogout);
      window.removeEventListener("storage", handleStorage);
    };
  }, [applyLoggedOut, beginAuthOperation, isCurrentAuthOperation, refreshUser]);

  // 登录
  const login = useCallback(
    async (credentials: LoginRequest, turnstileToken?: string) => {
      const owner = beginAuthOperation();
      if (isCurrentAuthOperation(owner)) setIsLoading(true);
      let sessionEstablished = false;
      try {
        await authApi.login(
          credentials,
          turnstileToken,
          owner.abortController.signal,
        );
        if (!isCurrentAuthOperation(owner)) return null;
        sessionEstablished = true;
        const currentUser = await authApi.getCurrentUser({
          signal: owner.abortController.signal,
        });
        if (!applyAuthenticatedUser(currentUser, owner)) return null;

        // 登录成功后，跳转到之前的页面
        const redirectPath = getRedirectPath();
        if (redirectPath) {
          clearRedirectPath();
        }
        return redirectPath ?? null;
      } catch (error) {
        if (!isCurrentAuthOperation(owner)) return null;
        if (sessionEstablished) {
          await authApi.logout(owner.abortController.signal);
        }
        throw error;
      } finally {
        if (isCurrentAuthOperation(owner)) setIsLoading(false);
      }
    },
    [applyAuthenticatedUser, beginAuthOperation, isCurrentAuthOperation],
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

  // OAuth 登录 - 直接导航到后端 OAuth 端点，由服务端重定向到提供商
  const loginWithOAuth = useCallback(async (provider: string) => {
    beginAuthOperation();
    window.location.href = buildOAuthLoginUrl(provider);
  }, [beginAuthOperation]);

  // 处理 OAuth 回调
  const handleOAuthCallback = useCallback(
    async (provider: string, code: string, state: string) => {
      const owner = beginAuthOperation();
      if (isCurrentAuthOperation(owner)) setIsLoading(true);
      let sessionEstablished = false;
      try {
        await authApi.handleOAuthCallback(
          provider,
          code,
          state,
          owner.abortController.signal,
        );
        if (!isCurrentAuthOperation(owner)) return;
        sessionEstablished = true;
        const currentUser = await authApi.getCurrentUser({
          signal: owner.abortController.signal,
        });
        applyAuthenticatedUser(currentUser, owner);
      } catch (error) {
        if (!isCurrentAuthOperation(owner)) return;
        if (sessionEstablished) {
          await authApi.logout(owner.abortController.signal);
        }
        throw error;
      } finally {
        if (isCurrentAuthOperation(owner)) setIsLoading(false);
      }
    },
    [applyAuthenticatedUser, beginAuthOperation, isCurrentAuthOperation],
  );

  // 登出
  const logout = useCallback(async () => {
    const owner = beginAuthOperation();
    if (isCurrentAuthOperation(owner)) setIsLoading(true);
    try {
      await authApi.logout(owner.abortController.signal);
      if (isCurrentAuthOperation(owner)) {
        applyLoggedOut(owner);
        setIsLoading(false);
      }
      return true;
    } catch (error) {
      if (!isCurrentAuthOperation(owner)) return true;
      console.error("[useAuth] Failed to logout:", error);
      setIsLoading(false);
      return false;
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
