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

// Auth Provider 组件
export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(getAccessToken());
  const [isLoading, setIsLoading] = useState(true);
  // 存储从 API 获取的动态权限
  const [dynamicPermissions, setDynamicPermissions] = useState<Permission[]>(
    [],
  );

  // 权限列表：从 API 动态获取
  const permissions = dynamicPermissions;

  const applyAuthenticatedUser = useCallback((currentUser: User) => {
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
  }, []);

  const clearLocalAuthView = useCallback(() => {
    setToken(null);
    setUser(null);
    setDynamicPermissions([]);
  }, []);

  const rollbackServerSession = useCallback(async () => {
    await authApi.logout();
  }, []);

  const refreshUser = useCallback(async () => {
    if (!isAuthenticated()) return;

    try {
      const currentUser = await authApi.getCurrentUser();
      applyAuthenticatedUser(currentUser);
    } catch (error) {
      if (error instanceof Error && /Unauthorized/i.test(error.message)) {
        clearLocalAuthView();
        return;
      }
      console.error("Failed to refresh user info:", error);
    }
  }, [applyAuthenticatedUser, clearLocalAuthView]);

  // 初始化：检查现有 token 并获取用户信息
  useEffect(() => {
    const initAuth = async () => {
      const hadSessionMarker = !!getAccessToken();

      try {
        const currentUser = await authApi.getCurrentUser();
        if (!hadSessionMarker) {
          setTokens("cookie-session");
        }
        applyAuthenticatedUser(currentUser);
        if (!hadSessionMarker) {
          window.dispatchEvent(new CustomEvent("auth:login"));
        }
      } catch (err) {
        if (err instanceof Error && /Unauthorized/i.test(err.message)) {
          clearLocalAuthView();
          setIsLoading(false);
          return;
        }
        console.warn("[useAuth] Failed to fetch current user:", err);
      }

      setIsLoading(false);
    };

    initAuth();
  }, [applyAuthenticatedUser, clearLocalAuthView]);

  // 监听登出事件
  useEffect(() => {
    const handleLogout = () => {
      clearLocalAuthView();
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
  }, [clearLocalAuthView, refreshUser]);

  // 登录
  const login = useCallback(
    async (credentials: LoginRequest, turnstileToken?: string) => {
      setIsLoading(true);
      try {
        await authApi.login(credentials, turnstileToken);

        try {
          const currentUser = await authApi.getCurrentUser();
          applyAuthenticatedUser(currentUser);
        } catch (error) {
          await rollbackServerSession();
          throw error;
        }

        // 登录成功后，跳转到之前的页面
        const redirectPath = getRedirectPath();
        if (redirectPath) {
          clearRedirectPath();
        }
        return redirectPath ?? null;
      } finally {
        setIsLoading(false);
      }
    },
    [applyAuthenticatedUser, rollbackServerSession],
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
    window.location.href = buildOAuthLoginUrl(provider);
  }, []);

  // 处理 OAuth 回调
  const handleOAuthCallback = useCallback(
    async (provider: string, code: string, state: string) => {
      setIsLoading(true);
      try {
        await authApi.handleOAuthCallback(provider, code, state);

        try {
          const currentUser = await authApi.getCurrentUser();
          applyAuthenticatedUser(currentUser);
        } catch (error) {
          await rollbackServerSession();
          throw error;
        }
      } finally {
        setIsLoading(false);
      }
    },
    [applyAuthenticatedUser, rollbackServerSession],
  );

  // 登出
  const logout = useCallback(async () => {
    try {
      await authApi.logout();
      return true;
    } catch (error) {
      console.error("[useAuth] Failed to logout:", error);
      return false;
    }
  }, []);

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
