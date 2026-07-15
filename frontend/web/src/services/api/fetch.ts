/** Cookie-session fetch wrapper with caller-owned identity recovery. */

import i18n from "i18next";
import { getAccessToken } from "./token";
import { projectSafeBackendError } from "../../utils/backendErrors";

// ============================================
// 带认证的 fetch 封装
// ============================================

interface FetchOptions extends RequestInit {
  skipAuth?: boolean;
}

/** A sanitized server status/code pair for callers that need safe recovery. */
export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

async function safeApiRequestError(
  response: Response,
  status = response.status,
): Promise<ApiRequestError> {
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
    status,
    i18n.t.bind(i18n),
  );
  return new ApiRequestError(projection.message, status, projection.code);
}

/**
 * 带认证的 fetch 封装
 * 浏览器生产路径只依赖同源 cookie session，不再附带脚本可读 bearer token。
 * 401/403 and forced re-login responses are returned as safe typed errors;
 * callers own any identity-state transition.
 */
export async function authFetch<T>(
  url: string,
  options: FetchOptions = {},
): Promise<T> {
  const {
    skipAuth = false,
    headers = {},
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

  // Cookie-session callers own identity recovery. This transport never
  // refreshes, replays, redirects, clears caches, or dispatches auth events.
  if (response.headers.get("X-Force-Relogin") === "true") {
    throw await safeApiRequestError(response, 401);
  }

  if (!response.ok) {
    throw await safeApiRequestError(response);
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
