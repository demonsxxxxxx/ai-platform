import {
  clearTokens,
  isSafeRedirectPath,
  setRedirectPath,
} from "./token";
import { clearAuthScopedCaches } from "./authCacheInvalidation";

export interface RefreshedTokens {
  access_token: string;
  refresh_token?: string;
}

export const COOKIE_SESSION_REFRESH_UNSUPPORTED_CODE =
  "cookie_session_refresh_unsupported";

/** Stable fail-closed result for obsolete browser refresh call sites. */
export class CookieSessionRefreshUnsupportedError extends Error {
  readonly code = COOKIE_SESSION_REFRESH_UNSUPPORTED_CODE;

  constructor() {
    super(COOKIE_SESSION_REFRESH_UNSUPPORTED_CODE);
    this.name = "CookieSessionRefreshUnsupportedError";
  }
}

function notifyLogout(): void {
  window.dispatchEvent(new CustomEvent("auth:logout"));
}

export function rememberRedirectPathForLogin(): void {
  if (typeof window === "undefined") {
    return;
  }
  const currentPath = window.location.pathname + window.location.search;
  if (isSafeRedirectPath(currentPath)) {
    setRedirectPath(currentPath);
  }
}

export function clearAuthState(): void {
  clearTokens();
  clearAuthScopedCaches();
  notifyLogout();
}

export function redirectToLogin(): void {
  // Compatibility firewall for legacy transport call sites. Identity recovery
  // belongs to the current AuthProvider owner and must not be global here.
}

/**
 * Get a valid (non-expired) access token.
 *
 * Returns `null` when no token exists — the caller decides what to do.
 * Browser production auth is cookie-based, so no bearer token is returned.
 */
export async function getValidAccessToken(): Promise<string | null> {
  return null;
}

/** Reject obsolete refresh callers without network or auth-state effects. */
export async function refreshTokens(): Promise<RefreshedTokens> {
  throw new CookieSessionRefreshUnsupportedError();
}

/** Reject obsolete access-token refresh callers through the same firewall. */
export async function refreshAccessToken(): Promise<string> {
  throw new CookieSessionRefreshUnsupportedError();
}
