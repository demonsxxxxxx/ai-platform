/**
 * Auth API - 认证相关
 */

import type { User, LoginRequest } from "../../types";
import { API_BASE } from "./config";
import { ApiRequestError, authFetch } from "./fetch";
import { projectSafeBackendError } from "../../utils/backendErrors";
import i18n from "../../i18n";

interface PrincipalResponseWire {
  user_id: string;
  user_name?: string;
  display_name: string;
  tenant_id: string;
  department_id: string;
  roles: string[];
  permissions: string[];
  is_admin: boolean;
  source: string;
}

export interface AuthContextBootstrapRequest {
  nonce: string;
  protocol_version: 2;
  browser_incarnation: string;
  generation: number;
  rotation_ticket?: string;
  recovery_only?: true;
}

export type AuthContextBootstrapResponse =
  | { status: "ready"; protocol_version: 2; generation: number }
  | {
      status: "rebootstrap_required";
      protocol_version: 2;
      generation: number;
      rotation_ticket: string;
    };

export type AuthContextBootstrapResult = AuthContextBootstrapResponse | void;

export type BootstrapAuthContext = {
  bivarianceHack(
    request: AuthContextBootstrapRequest,
    signal?: AbortSignal,
  ): Promise<AuthContextBootstrapResult>;
}["bivarianceHack"];

async function bootstrapAuthContext(
  request: AuthContextBootstrapRequest,
  signal?: AbortSignal,
): Promise<AuthContextBootstrapResult> {
  return authFetch<AuthContextBootstrapResponse>(`${API_BASE}/api/ai/auth/bootstrap`, {
    method: "POST",
    skipAuth: true,
    credentials: "include",
    body: JSON.stringify(request),
    headers: { "Content-Type": "application/json" },
    signal: withAuthRequestTimeout(signal),
  });
}

function mapPrincipalToUser(principal: PrincipalResponseWire): User {
  return {
    id: principal.user_id,
    tenant_id: principal.tenant_id,
    department_id: principal.department_id,
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

const AUTH_REQUEST_TIMEOUT_MS = 15_000;

function withAuthRequestTimeout(signal?: AbortSignal): AbortSignal {
  const timeout = AbortSignal.timeout(AUTH_REQUEST_TIMEOUT_MS);
  return signal ? AbortSignal.any([signal, timeout]) : timeout;
}

export class CompanyADLoginError extends Error {
  constructor() {
    super("company_ad_login_failed");
    this.name = "CompanyADLoginError";
  }
}

async function fetchCompanyADLogin(
  loginUrl: string,
  signal?: AbortSignal,
): Promise<string> {
  const requestSignal = withAuthRequestTimeout(signal);
  let response: Response;
  try {
    response = await fetch(loginUrl, {
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: requestSignal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new CompanyADLoginError();
  }
  if (!response.ok) throw new CompanyADLoginError();

  const payload: unknown = await response.json().catch(() => null);
  const firstResult = Array.isArray(payload) ? payload[0] : null;
  const token =
    firstResult && typeof firstResult === "object"
      ? (firstResult as { token?: unknown }).token
      : null;
  if (typeof token !== "string" || !token.trim()) {
    throw new CompanyADLoginError();
  }
  return token.trim();
}

export const authApi = {
  /**
   * Establish the stable HttpOnly browser auth context before any mutation.
   */
  bootstrapAuthContext: bootstrapAuthContext as BootstrapAuthContext,

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
        signal: withAuthRequestTimeout(signal),
      },
    );

  },

  /** Read the canonical platform configuration for optional Windows login. */
  async getADLoginConfig(signal?: AbortSignal): Promise<{
    ad_login_url: string | null;
  }> {
    return authFetch<{ ad_login_url: string | null }>(
      `${API_BASE}/api/ai/auth/ad-login/config`,
      {
        skipAuth: true,
        signal: withAuthRequestTimeout(signal),
      },
    );
  },

  /** Obtain the browser's Windows-authenticated company JWT. */
  async fetchCompanyADLogin(
    loginUrl: string,
    signal?: AbortSignal,
  ): Promise<string> {
    return fetchCompanyADLogin(loginUrl, signal);
  },

  /** Exchange one company-authenticated JWT for a platform session. */
  async loginWithAD(companyJwt: string, signal?: AbortSignal): Promise<void> {
    await authFetch<PrincipalResponseWire>(
      `${API_BASE}/api/ai/auth/ad-login`,
      {
        method: "POST",
        skipAuth: true,
        credentials: "include",
        body: JSON.stringify({ token: companyJwt }),
        headers: { "Content-Type": "application/json" },
        signal: withAuthRequestTimeout(signal),
      },
    );
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
        signal: withAuthRequestTimeout(options.signal),
      },
    );
    return mapPrincipalToUser(principal);
  },

  /** Read the current user's company credential for the document-translator handoff. */
  async getCompanyCredentialForHandoff(signal?: AbortSignal): Promise<string> {
    const payload = await authFetch<{ credential?: unknown }>(
      `${API_BASE}/api/ai/auth/company-credential-handoff`,
      {
        method: "POST",
        skipAuth: true,
        credentials: "include",
        cache: "no-store",
        signal,
      },
    );
    if (typeof payload.credential !== "string" || !payload.credential.trim()) {
      throw new Error("company_credential_unavailable");
    }
    return payload.credential.trim();
  },

  /**
   * 登出
   */
  async logout(signal?: AbortSignal): Promise<void> {
    const response = await fetch(`${API_BASE}/api/ai/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: {
        "Accept-Language": "zh-CN",
      },
      signal: withAuthRequestTimeout(signal),
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
      i18n.getFixedT("zh"),
    );
    throw new ApiRequestError(
      projection.message,
      response.status,
      projection.code,
    );
  },

  /**
   * 获取用户个人资料
   */
  async getProfile(options: { signal?: AbortSignal } = {}): Promise<User> {
    return authFetch<User>(`${API_BASE}/api/auth/profile`, {
      signal: options.signal,
    });
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
    turnstile?: {
      enabled: boolean;
      site_key: string;
      require_on_login: boolean;
    };
  }> {
    return authFetch<{
      providers: { id: string; name: string }[];
      turnstile?: {
        enabled: boolean;
        site_key: string;
        require_on_login: boolean;
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
        signal: withAuthRequestTimeout(signal),
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
        signal: withAuthRequestTimeout(signal),
      },
    );
  },
};
