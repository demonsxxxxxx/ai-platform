/**
 * Auth API - 认证相关
 */

import type {
  User,
  UserCreate,
  LoginRequest,
  TokenResponse,
  PermissionsResponse,
  RegisterResponse,
} from "../../types";
import { API_BASE } from "./config";
import { ApiRequestError, authFetch } from "./fetch";
import { refreshTokens } from "./tokenManager";
import { projectSafeBackendError } from "../../utils/backendErrors";
import i18n from "../../i18n";

interface PrincipalResponseWire {
  user_id: string;
  user_name?: string;
  display_name: string;
  tenant_id: string;
  roles: string[];
  permissions: string[];
  is_admin: boolean;
  source: string;
}

function mapPrincipalToUser(principal: PrincipalResponseWire): User {
  return {
    id: principal.user_id,
    tenant_id: principal.tenant_id,
    username: principal.user_name || principal.user_id,
    email: "",
    avatar_url: undefined,
    roles: principal.roles,
    permissions: principal.permissions,
    is_admin: principal.is_admin,
    is_active: true,
    metadata: {
      display_name: principal.display_name,
      source: principal.source,
    },
    created_at: "",
    updated_at: "",
  };
}

export function buildOAuthLoginUrl(provider: string, state?: string): string {
  const safeProvider = encodeURIComponent(provider);
  const suffix = state ? `?state=${encodeURIComponent(state)}` : "";
  return `${API_BASE}/api/auth/oauth/${safeProvider}${suffix}`;
}

export const authApi = {
  /**
   * Establish the stable HttpOnly browser auth context before any mutation.
   */
  async bootstrapAuthContext(nonce: string, signal?: AbortSignal): Promise<void> {
    await authFetch<{ status: string }>(`${API_BASE}/api/ai/auth/bootstrap`, {
      method: "POST",
      skipAuth: true,
      credentials: "include",
      body: JSON.stringify({ nonce }),
      headers: { "Content-Type": "application/json" },
      signal,
    });
  },

  /**
   * 用户登录
   */
  async login(
    credentials: LoginRequest,
    turnstileToken?: string,
    signal?: AbortSignal,
  ): Promise<void> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (turnstileToken) {
      headers["X-Turnstile-Token"] = turnstileToken;
    }

    await authFetch<PrincipalResponseWire>(
      `${API_BASE}/api/ai/auth/login`,
      {
        method: "POST",
        skipAuth: true,
        credentials: "include",
        body: JSON.stringify(credentials),
        headers,
        signal,
      },
    );

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
  async getCurrentUser(options: { signal?: AbortSignal } = {}): Promise<User> {
    const principal = await authFetch<PrincipalResponseWire>(
      `${API_BASE}/api/ai/auth/me`,
      {
        skipAuth: true,
        credentials: "include",
        signal: options.signal,
      },
    );
    return mapPrincipalToUser(principal);
  },

  /**
   * 登出
   */
  async logout(signal?: AbortSignal): Promise<void> {
    const response = await fetch(`${API_BASE}/api/ai/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Accept-Language": i18n.language || "en",
      },
      signal,
    });
    if (response.ok || response.status === 401 || response.status === 403) return;

    const payload: unknown = await response.json().catch(() => null);
    const detail =
      payload !== null &&
      typeof payload === "object" &&
      !Array.isArray(payload) &&
      Object.prototype.hasOwnProperty.call(payload, "detail")
        ? (payload as { detail?: unknown }).detail
        : undefined;
    const projection = projectSafeBackendError(
      detail,
      response.status,
      i18n.t.bind(i18n),
    );
    throw new ApiRequestError(
      projection.message,
      response.status,
      projection.code,
    );
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
   * Begin a server-fenced OAuth operation and receive opaque provider state.
   */
  async beginOAuth(
    provider: string,
    signal?: AbortSignal,
  ): Promise<{ state: string }> {
    return authFetch<{ state: string }>(
      `${API_BASE}/api/ai/auth/oauth/${encodeURIComponent(provider)}/begin`,
      {
        method: "POST",
        skipAuth: true,
        credentials: "include",
        signal,
      },
    );
  },

  /**
   * 处理 OAuth 回调
   */
  async handleOAuthCallback(
    provider: string,
    code: string,
    state: string,
    signal?: AbortSignal,
  ): Promise<void> {
    await authFetch<Record<string, unknown>>(
      `${API_BASE}/api/ai/auth/oauth/${encodeURIComponent(provider)}/callback`,
      {
        method: "POST",
        skipAuth: true,
        credentials: "include",
        body: JSON.stringify({ code, state }),
        signal,
      },
    );
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
