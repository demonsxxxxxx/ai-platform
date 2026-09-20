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
