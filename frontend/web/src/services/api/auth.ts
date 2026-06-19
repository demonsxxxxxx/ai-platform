/**
 * Auth API - 认证相关
 */

import type {
  User,
  UserCreate,
  LoginRequest,
  PrincipalResponse,
  TokenResponse,
  PermissionsResponse,
  RegisterResponse,
} from "../../types";
import { API_BASE } from "./config";
import { clearAuthScopedCaches } from "./authCacheInvalidation";
import { authFetch } from "./fetch";
import { clearAuthState, refreshTokens } from "./tokenManager";

export function buildOAuthLoginUrl(provider: string): string {
  return `${API_BASE}/api/auth/oauth/${provider}`;
}

function principalToUser(principal: PrincipalResponse): User {
  const username = principal.user_name || principal.user_id;
  return {
    id: principal.user_id,
    username,
    email: "",
    avatar_url: undefined,
    roles: principal.roles,
    permissions: principal.permissions,
    is_active: true,
    metadata: {
      display_name: principal.display_name,
      tenant_id: principal.tenant_id,
      is_admin: principal.is_admin,
      source: principal.source,
    },
    created_at: "",
    updated_at: "",
  };
}

export const authApi = {
  /**
   * 用户登录
   */
  async login(
    credentials: LoginRequest,
    turnstileToken?: string,
  ): Promise<PrincipalResponse> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (turnstileToken) {
      headers["X-Turnstile-Token"] = turnstileToken;
    }

    const response = await authFetch<PrincipalResponse>(
      `${API_BASE}/api/ai/auth/login`,
      {
        method: "POST",
        skipAuth: true,
        credentials: "include",
        body: JSON.stringify(credentials),
        headers,
      },
    );

    clearAuthScopedCaches();
    window.dispatchEvent(new CustomEvent("auth:login"));

    return response;
  },

  /**
   * 用户注册
   */
  async register(
    userData: UserCreate,
    turnstileToken?: string,
  ): Promise<RegisterResponse> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (turnstileToken) {
      headers["X-Turnstile-Token"] = turnstileToken;
    }

    return authFetch<RegisterResponse>(`${API_BASE}/api/auth/register`, {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify(userData),
      headers,
    });
  },

  /**
   * 刷新 token
   */
  async refreshToken(): Promise<TokenResponse> {
    const { access_token, refresh_token } = await refreshTokens();
    return {
      access_token,
      refresh_token,
      token_type: "bearer",
    };
  },

  /**
   * 获取当前用户信息
   */
  async getCurrentUser(): Promise<User> {
    const principal = await authFetch<PrincipalResponse>(
      `${API_BASE}/api/ai/auth/me`,
      { credentials: "include" },
    );
    return principalToUser(principal);
  },

  /**
   * 登出
   */
  async logout(): Promise<void> {
    try {
      await authFetch<{ status: string }>(`${API_BASE}/api/ai/auth/logout`, {
        method: "POST",
        credentials: "include",
        skipAuth: true,
      });
    } finally {
      clearAuthState();
    }
  },

  /**
   * 获取所有可用权限列表
   */
  async getPermissions(): Promise<PermissionsResponse> {
    return authFetch<PermissionsResponse>(`${API_BASE}/api/auth/permissions`, {
      skipAuth: true,
    });
  },

  /**
   * 更新头像
   */
  async updateAvatar(avatarUrl: string): Promise<User> {
    return authFetch<User>(`${API_BASE}/api/auth/update-avatar`, {
      method: "POST",
      body: JSON.stringify({ avatar_url: avatarUrl }),
    });
  },

  /**
   * 更新用户名
   */
  async updateUsername(username: string): Promise<User> {
    return authFetch<User>(`${API_BASE}/api/auth/update-username`, {
      method: "POST",
      body: JSON.stringify({ username }),
    });
  },

  /**
   * 获取用户个人资料
   */
  async getProfile(): Promise<User> {
    return authFetch<User>(`${API_BASE}/api/auth/profile`);
  },

  /**
   * 更新用户偏好 metadata（部分合并）
   */
  async updateMetadata(metadata: Record<string, unknown>): Promise<User> {
    return authFetch<User>(`${API_BASE}/api/auth/profile/metadata`, {
      method: "PUT",
      body: JSON.stringify({ metadata }),
    });
  },

  /**
   * 获取可用的 OAuth 提供商列表
   */
  async getOAuthProviders(): Promise<{
    providers: { id: string; name: string }[];
    registration_enabled: boolean;
    turnstile?: {
      enabled: boolean;
      site_key: string;
      require_on_login: boolean;
      require_on_register: boolean;
      require_on_password_change: boolean;
    };
  }> {
    return authFetch<{
      providers: { id: string; name: string }[];
      registration_enabled: boolean;
      turnstile?: {
        enabled: boolean;
        site_key: string;
        require_on_login: boolean;
        require_on_register: boolean;
        require_on_password_change: boolean;
      };
    }>(`${API_BASE}/api/auth/oauth/providers`, { skipAuth: true });
  },

  /**
   * 处理 OAuth 回调
   */
  async handleOAuthCallback(
    _provider: string,
    _code: string,
    _state: string,
  ): Promise<TokenResponse> {
    throw new Error("OAuth login is scheduled for Phase 2");
  },

  /**
   * 忘记密码 - 发送重置邮件
   */
  async forgotPassword(email: string): Promise<{ message: string }> {
    return authFetch<{ message: string }>(
      `${API_BASE}/api/auth/forgot-password`,
      {
        method: "POST",
        skipAuth: true,
        body: JSON.stringify({ email }),
      },
    );
  },

  /**
   * 重置密码
   */
  async resetPassword(
    token: string,
    newPassword: string,
  ): Promise<{ message: string }> {
    return authFetch<{ message: string }>(
      `${API_BASE}/api/auth/reset-password`,
      {
        method: "POST",
        skipAuth: true,
        body: JSON.stringify({ token, new_password: newPassword }),
      },
    );
  },

  /**
   * 验证邮箱
   */
  async verifyEmail(token: string): Promise<{ message: string }> {
    return authFetch<{ message: string }>(`${API_BASE}/api/auth/verify-email`, {
      method: "POST",
      skipAuth: true,
      body: JSON.stringify({ token }),
    });
  },

  /**
   * 重发验证邮件
   */
  async resendVerification(email: string): Promise<{ message: string }> {
    return authFetch<{ message: string }>(
      `${API_BASE}/api/auth/resend-verification`,
      {
        method: "POST",
        skipAuth: true,
        body: JSON.stringify({ email }),
      },
    );
  },

  /**
   * 修改密码
   */
  async changePassword(
    oldPassword: string,
    newPassword: string,
  ): Promise<{ message: string }> {
    return authFetch<{ message: string }>(
      `${API_BASE}/api/auth/change-password`,
      {
        method: "POST",
        body: JSON.stringify({
          old_password: oldPassword,
          new_password: newPassword,
        }),
      },
    );
  },
};
