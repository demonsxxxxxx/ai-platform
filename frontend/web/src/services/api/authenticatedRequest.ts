import { redirectToLogin } from "./tokenManager";

interface AuthenticatedRequestOptions extends RequestInit {
  retryOn401?: boolean;
}

export async function createAuthHeaders(
  headers: HeadersInit = {},
): Promise<Headers> {
  return new Headers(headers);
}

/**
 * Authenticated request with automatic 401 retry.
 * Behavior is consistent with authFetch: throws on auth failure.
 */
export async function authenticatedRequest(
  input: RequestInfo | URL,
  init: AuthenticatedRequestOptions = {},
): Promise<Response> {
  const { retryOn401 = true, headers = {}, ...rest } = init;
  const finalHeaders = await createAuthHeaders(headers);
  const response = await fetch(input, {
    ...rest,
    headers: finalHeaders,
    credentials: rest.credentials ?? "include",
  });

  if (response.status !== 401 || !retryOn401) {
    return response;
  }

  redirectToLogin();
  throw new Error("Unauthorized");
}
