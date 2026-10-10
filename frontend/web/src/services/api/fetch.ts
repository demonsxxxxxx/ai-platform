/** Cookie-session fetch wrapper with caller-owned identity recovery. */

import i18n from "../../i18n";
import { projectSafeBackendError } from "../../utils/backendErrors";
import { getAccessToken } from "./token";

// ============================================
// 带认证的 fetch 封装
// ============================================

interface FetchOptions extends RequestInit {
  skipAuth?: boolean;
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
  finalHeaders.set("Accept-Language", "zh-CN");
  finalHeaders.delete("Authorization");
  // Retained only as a source-compatible option for public auth endpoints.
  // Cookie-session transport behavior is identical either way.
  void skipAuth;

  const requestAuthMarker = getAccessToken();
  const response = await cookieSessionFetch(url, {
    ...restOptions,
    headers: finalHeaders,
  });

  // Cookie-session callers own identity recovery. Only the request's current
  // auth incarnation may invalidate the browser presentation; an older
  // response must not log out a replacement principal.
  const authMarkerIsCurrent = requestAuthMarker === getAccessToken();
  const forceReloginHeader =
    response.headers.get("X-Force-Relogin") === "true";
  if (
    (response.status === 401 &&
      requestAuthMarker !== null &&
      authMarkerIsCurrent) ||
    (forceReloginHeader && authMarkerIsCurrent)
  ) {
    const error = await apiRequestErrorFromResponse(response, 401);
    // Body consumption is asynchronous too; the original response-arrival
    // fence cannot authorize invalidating a later browser identity.
    if (requestAuthMarker === getAccessToken()) notifyForcedRelogin();
    throw error;
  }

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
