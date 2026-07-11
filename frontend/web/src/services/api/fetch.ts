/**
 * Authenticated fetch wrapper with token refresh support
 */

import i18n from "i18next";
import { getAccessToken, getRefreshToken } from "./token";
import {
  redirectToLogin,
  rememberRedirectPathForLogin,
  refreshAccessToken,
  clearAuthState,
} from "./tokenManager";
import { translateBackendError } from "../../utils/backendErrors";

// ============================================
// 带认证的 fetch 封装
// ============================================

interface FetchOptions extends RequestInit {
  skipAuth?: boolean;
  _retry?: boolean;
}

/**
 * 带认证的 fetch 封装
 * 浏览器生产路径只依赖同源 cookie session，不再附带脚本可读 bearer token。
 * 处理 401 响应
 */
export async function authFetch<T>(
  url: string,
  options: FetchOptions = {},
): Promise<T> {
  const {
    skipAuth = false,
    headers = {},
    _retry = false,
    ...restOptions
  } = options;

  const finalHeaders = new Headers(headers);
  if (!(restOptions.body instanceof FormData)) {
    finalHeaders.set("Content-Type", "application/json");
  }
  finalHeaders.set("Accept-Language", i18n.language || "en");
  finalHeaders.delete("Authorization");

  if (!skipAuth) {
    // Touch the marker path so legacy bearer keys are eagerly scrubbed.
    getAccessToken();
  }

  const response = await fetch(url, {
    ...restOptions,
    credentials: restOptions.credentials ?? "include",
    headers: finalHeaders,
  });

  // 检查当前用户是否被修改（需要重新登录）
  if (!skipAuth && response.headers.get("X-Force-Relogin") === "true") {
    clearAuthState();
    throw new Error("用户权限已变更，请重新登录");
  }

  // 处理 401 未授权响应
  if (response.status === 401 && !skipAuth) {
    if (getRefreshToken() && !_retry) {
      try {
        await refreshAccessToken();
      } catch (error) {
        if (error instanceof Error && /Unauthorized/i.test(error.message)) {
          rememberRedirectPathForLogin();
        } else {
          redirectToLogin();
        }
        throw error;
      }
      return authFetch<T>(url, { ...options, skipAuth: false, _retry: true });
    }

    redirectToLogin();
    throw new Error("Unauthorized");
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    // 处理 detail 为对象或字符串的情况
    let errorMessage: string;
    if (typeof errorData.detail === "object" && errorData.detail !== null) {
      // 如果 detail 是对象，提取 message 字段
      errorMessage =
        errorData.detail.message || JSON.stringify(errorData.detail);
    } else {
      errorMessage =
        errorData.detail || `Request failed: ${response.statusText}`;
    }
    throw new Error(translateBackendError(errorMessage, i18n.t.bind(i18n)));
  }

  // 处理空响应
  // 注意：当响应体为空时返回 null，调用者应处理 T | null 的情况
  // 对于必须返回非空值的场景，API 应确保返回空对象 {} 而不是空响应
  const text = await response.text();
  if (!text) {
    return null as T;
  }

  try {
    return JSON.parse(text) as T;
  } catch {
    console.warn("[authFetch] Failed to parse response as JSON");
    return null as T;
  }
}
