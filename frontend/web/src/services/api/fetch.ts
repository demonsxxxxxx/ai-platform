/** Cookie-session fetch wrapper with caller-owned identity recovery. */

import i18n from "../../i18n";
import { projectSafeBackendError } from "../../utils/backendErrors";
import { getAccessToken } from "./token";
import { AuthSessionChangedError, refreshTokens } from "./tokenManager";

// ============================================
// 带认证的 fetch 封装
// ============================================

interface FetchOptions extends RequestInit {
  skipAuth?: boolean;
  /** Principal hydration already has an auth-operation owner for ordinary 401. */
  ordinary401Recovery?: "global" | "caller";
}

/** A sanitized server status/code pair for callers that need safe recovery. */
export const FORCE_RELOGIN_EVENT = "auth:force-relogin";

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
    readonly submissionDisposition?: "rejected_before_persist",
    readonly diagnosticId?: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

/** A success status without the promised JSON does not prove a write failed. */
export class ApiProtocolError extends ApiRequestError {
  constructor(status: number) {
    const projection = projectSafeBackendError(
      { code: "api_response_invalid" },
      status,
      i18n.getFixedT("zh"),
    );
    super(projection.message, status, projection.code);
    this.name = "ApiProtocolError";
  }
}

export function notifyForcedRelogin(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(FORCE_RELOGIN_EVENT));
  }
}

/** Convert an untrusted HTTP response into the shared safe error contract. */
export async function apiRequestErrorFromResponse(
  response: Response,
  status = response.status,
): Promise<ApiRequestError> {
  // Only JSON parsing can fall back to the safe HTTP-status copy. A failed or
  // cancelled body read must keep its original identity, including custom
  // AbortSignal reasons, without triggering browser identity recovery.
  const text = await response.text();
  let payload: unknown = null;
  try {
    payload = JSON.parse(text);
  } catch {
    // Malformed error bodies cannot provide public presentation authority.
  }
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
    i18n.getFixedT("zh"),
  );
  const submissionDisposition =
    detail !== null &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    (detail as { submission_disposition?: unknown }).submission_disposition ===
      "rejected_before_persist"
      ? "rejected_before_persist"
      : undefined;
  const diagnosticId =
    detail !== null &&
    typeof detail === "object" &&
    !Array.isArray(detail) &&
    typeof (detail as { diagnostic_id?: unknown }).diagnostic_id === "string" &&
    /^diag_[a-f0-9]{16}$/.test(
      (detail as { diagnostic_id: string }).diagnostic_id,
    )
      ? (detail as { diagnostic_id: string }).diagnostic_id
      : undefined;
  return new ApiRequestError(
    projection.message,
    status,
    projection.code,
    submissionDisposition,
    diagnosticId,
  );
}

/**
 * The single browser cookie-session transport seam.
 *
 * It performs exactly one request and never refreshes, replays, redirects, or
 * mutates browser auth state. Callers own response interpretation.
 */
export async function cookieSessionFetch(
  input: RequestInfo | URL,
  options: RequestInit = {},
): Promise<Response> {
  const headers = new Headers(options.headers);
  headers.set("Accept-Language", "zh-CN");
  headers.delete("Authorization");
  return fetch(input, {
    ...options,
    credentials: options.credentials ?? "include",
    headers,
  });
}

const REFRESHABLE_AUTH_CODES = new Set([
  "company_login_expired", "mcp_principal_jwt_expired", "mcp_principal_jwt_missing",
  "mcp_jwt_expired_or_missing",
]);

export function isRefreshableAuthRejection(error: ApiRequestError, forced: boolean): boolean {
  return !forced || REFRESHABLE_AUTH_CODES.has(error.code ?? "");
}

/** Refresh only authentication rejections; never replay an ambiguous mutation. */
export async function cookieSessionRequest(
  input: RequestInfo | URL,
  options: RequestInit = {},
  ordinary401Recovery: "global" | "caller" = "global",
): Promise<Response> {
  const marker = getAccessToken();
  const originalRequest = input instanceof Request ? input : null;
  const replayRequest = originalRequest?.clone();
  const signal = options.signal ?? originalRequest?.signal;
  const method = (options.method ?? originalRequest?.method ?? "GET").toUpperCase();
  const originalBody = options.body;
  const body = originalBody instanceof FormData
    ? (() => {
      const copy = new FormData();
      originalBody.forEach((value, key) => copy.append(key, value));
      return copy;
    })()
    : originalBody instanceof URLSearchParams ? new URLSearchParams(originalBody) : originalBody;
  const requestOptions = { ...options, body };
  const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  const path = new URL(rawUrl, "http://localhost").pathname;
  const authEndpoint = /^\/api\/(?:ai\/)?auth\/(?:login|logout|bootstrap|refresh|ad-login|oauth)(?:\/|$)/.test(path);
  const response = await cookieSessionFetch(input, requestOptions);
  const forced = response.headers.get("X-Force-Relogin") === "true";
  if (response.status === 409 && marker && !authEndpoint && ordinary401Recovery === "global") {
    const conflict = await apiRequestErrorFromResponse(response.clone());
    signal?.throwIfAborted();
    if (marker === getAccessToken() && conflict.code === "auth_context_stale") {
      notifyForcedRelogin();
      throw new ApiRequestError(conflict.message, 401, conflict.code);
    }
  }
  if (response.status !== 401 && !forced) return response;
  const error = await apiRequestErrorFromResponse(response, forced ? 401 : response.status);
  signal?.throwIfAborted();
  if (marker !== getAccessToken()) throw error;
  if (authEndpoint) throw error;
  const refreshable = isRefreshableAuthRejection(error, forced);
  if (marker && refreshable) {
    try {
      await refreshTokens(signal ?? undefined);
    } catch (refreshError) {
      signal?.throwIfAborted();
      if (marker !== getAccessToken()) throw new AuthSessionChangedError();
      if (ordinary401Recovery === "global" && refreshError instanceof ApiRequestError
        && (refreshError.status === 401 || refreshError.status === 403)) {
        notifyForcedRelogin();
      }
      throw refreshError;
    }
    signal?.throwIfAborted();
    if (marker !== getAccessToken()) throw new AuthSessionChangedError();
    const rejectedBeforeExecution = response.headers.get("X-Auth-Rejected-Before-Execution") === "true"
      || error.submissionDisposition === "rejected_before_persist";
    const streamBody = typeof ReadableStream !== "undefined" && body instanceof ReadableStream;
    if (!streamBody && (method === "GET" || method === "HEAD" || rejectedBeforeExecution)) {
      const retried = await cookieSessionFetch(replayRequest ?? input, requestOptions);
      const retryForced = retried.headers.get("X-Force-Relogin") === "true";
      if (retried.status !== 401 && !retryForced) return retried;
      const retryError = await apiRequestErrorFromResponse(retried, 401);
      signal?.throwIfAborted();
      if (marker !== getAccessToken()) throw new AuthSessionChangedError();
      if (ordinary401Recovery === "global") notifyForcedRelogin();
      throw retryError;
    }
    throw error;
  }
  if (forced || (ordinary401Recovery === "global" && marker)) notifyForcedRelogin();
  throw error;
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
    ordinary401Recovery = "global",
    headers = {},
    ...restOptions
  } = options;

  const finalHeaders = new Headers(headers);
  if (!(restOptions.body instanceof FormData)) {
    finalHeaders.set("Content-Type", "application/json");
  }
  finalHeaders.set("Accept-Language", "zh-CN");
  finalHeaders.delete("Authorization");
  // Retained only as a source-compatible option for public auth endpoints.
  // Cookie-session transport behavior is identical either way.
  void skipAuth;

  const response = await cookieSessionRequest(url, {
    ...restOptions,
    headers: finalHeaders,
  }, ordinary401Recovery);

  if (!response.ok) {
    throw await apiRequestErrorFromResponse(response);
  }

  // Only HTTP contracts that prohibit response content may omit JSON. Keep
  // their existing null result; an empty ordinary success could be truncation.
  if (
    response.status === 204 ||
    response.status === 205 ||
    restOptions.method?.toUpperCase() === "HEAD"
  ) {
    return null as T;
  }

  // Read outside the parse catch so transport failures and AbortError retain
  // their original identity. Neither those failures nor malformed JSON permit
  // replay: the server may already have committed a mutation.
  const text = await response.text();
  try {
    return JSON.parse(text) as T;
  } catch {
    // Never retain the parser's message, raw body, URL, or private diagnostics.
    throw new ApiProtocolError(response.status);
  }
}
