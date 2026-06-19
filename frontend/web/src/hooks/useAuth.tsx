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
  getAccessToken,
  getRedirectPath,
  clearRedirectPath,
} from "../services/api";
import { DEFAULT_THINKING_LEVEL_STORAGE_KEY } from "../components/layout/AppContent/useAgentOptions";
import { normalizePrincipalPermissions } from "../auth/aiPlatformPermissions";
import { Permission } from "../types";
import type { User, UserCreate, LoginRequest, AuthState } from "../types";
import i18n from "../i18n";

export const SIDEBAR_COLLAPSED_STORAGE_KEY = "lamb-sidebar-collapsed";

/** Apply user metadata preferences from backend */
function applyUserMetadata(metadata?: {
  language?: string;
  theme?: string;
  defaultThinkingLevel?: string;
  defaultAgentId?: string;
  sidebarCollapsed?: string;
}) {
  if (!metadata) return;

  if (metadata.language) {
    localStorage.setItem("language", metadata.language);
    i18n.changeLanguage(metadata.language);
  }

  if (metadata.theme) {
    localStorage.setItem("lamb-agent-theme", metadata.theme);
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

  if (metadata.defaultAgentId) {
    localStorage.setItem("defaultAgentId", metadata.defaultAgentId);
    window.dispatchEvent(new CustomEvent("agent-preference-updated"));
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
  logout: () => void;
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
    setUser(currentUser);
    applyUserMetadata(currentUser.metadata);
    setDynamicPermissions(
      normalizePrincipalPermissions(currentUser.permissions),
    );
  }, []);

  // 初始化：用 httpOnly cookie 恢复后端 principal
  useEffect(() => {
    const initAuth = async () => {
      try {
        const currentUser = await authApi.getCurrentUser();
        applyAuthenticatedUser(currentUser);
      } catch (err) {
        // Only treat 401 as auth failure — network errors / server restarts
        // should NOT clear auth state during development.
        // authFetch already handles 401 by calling redirectToLogin internally,
        // so this catch only fires for non-401 errors.
        console.warn("[useAuth] Failed to fetch current user:", err);
      }

      setIsLoading(false);
    };

    initAuth();
  }, [applyAuthenticatedUser]);

  // 监听登出事件
  useEffect(() => {
    const handleLogout = () => {
      setToken(null);
      setUser(null);
      setDynamicPermissions([]);
    };

    window.addEventListener("auth:logout", handleLogout);
    return () => window.removeEventListener("auth:logout", handleLogout);
  }, []);

  // 登录
  const login = useCallback(
    async (credentials: LoginRequest, turnstileToken?: string) => {
      setIsLoading(true);
      try {
        await authApi.login(credentials, turnstileToken);
        const currentUser = await authApi.getCurrentUser();
        applyAuthenticatedUser(currentUser);
        setToken(getAccessToken());

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
    [applyAuthenticatedUser],
  );

  const register = useCallback(
    async (
      userData: UserCreate,
      turnstileToken?: string,
    ): Promise<{ requiresVerification: boolean; email: string }> => {
      void userData;
      void turnstileToken;
      throw new Error("Self-service registration is scheduled for Phase 2");
    },
    [],
  );

  const loginWithOAuth = useCallback(async (provider: string) => {
    void provider;
    throw new Error("OAuth login is scheduled for Phase 2");
  }, []);

  const handleOAuthCallback = useCallback(
    async (provider: string, code: string, state: string) => {
      void provider;
      void code;
      void state;
      throw new Error("OAuth login is scheduled for Phase 2");
    },
    [],
  );

  // 登出
  const logout = useCallback(async () => {
    await authApi.logout();
    setToken(null);
    setUser(null);
    setDynamicPermissions([]);
  }, []);

  // 刷新用户信息（同时更新动态权限）
  const refreshUser = useCallback(async () => {
    try {
      const currentUser = await authApi.getCurrentUser();
      applyAuthenticatedUser(currentUser);
      setToken(getAccessToken());
    } catch (error) {
      console.error("Failed to refresh user info:", error);
    }
  }, [applyAuthenticatedUser]);

  // 检查是否拥有某个权限
  const hasPermission = useCallback(
    (permission: Permission): boolean => {
      return permissions.includes(permission);
    },
    [permissions],
  );

  // 检查是否拥有任意一个权限
  const hasAnyPermission = useCallback(
    (perms: Permission[]): boolean => {
      return perms.some((p) => permissions.includes(p));
    },
    [permissions],
  );

  // 检查是否拥有所有权限
  const hasAllPermissions = useCallback(
    (perms: Permission[]): boolean => {
      return perms.every((p) => permissions.includes(p));
    },
    [permissions],
  );

  const value: AuthContextType = {
    user,
    token,
    isAuthenticated: !!user,
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
