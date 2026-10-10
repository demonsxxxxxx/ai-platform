import {
  apiRequestErrorFromResponse,
  cookieSessionFetch,
  notifyForcedRelogin,
} from "./fetch";
import { getAccessToken } from "./token";

export async function createAuthHeaders(
  headers: HeadersInit = {},
): Promise<Headers> {
  const authHeaders = new Headers(headers);
  authHeaders.delete("Authorization");
  return authHeaders;
}

/**
 * Raw response adapter for the single cookie-session transport seam.
 * Authentication failures are safe typed errors; all other statuses remain
 * available to response-oriented callers.
 */
export async function authenticatedRequest(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const requestAuthMarker = getAccessToken();
  const { headers = {}, ...rest } = init;
  const finalHeaders = await createAuthHeaders(headers);
  const response = await cookieSessionFetch(input, {
    ...rest,
    headers: finalHeaders,
  });

  if (response.headers.get("X-Force-Relogin") === "true") {
    const error = await apiRequestErrorFromResponse(response, 401);
    // The response and its body may both arrive after another identity signs in.
    if (requestAuthMarker === getAccessToken()) notifyForcedRelogin();
    throw error;
  }
  if (response.status === 401 || response.status === 403) {
    throw await apiRequestErrorFromResponse(response);
  }
  return response;
}
