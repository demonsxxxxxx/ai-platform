import {
  apiRequestErrorFromResponse,
  cookieSessionRequest,
} from "./fetch";

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
  const { headers = {}, ...rest } = init;
  const finalHeaders = await createAuthHeaders(headers);
  const response = await cookieSessionRequest(input, {
    ...rest,
    headers: finalHeaders,
  });

  if (response.status === 403) {
    throw await apiRequestErrorFromResponse(response);
  }
  return response;
}
