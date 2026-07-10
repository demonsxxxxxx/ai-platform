import { API_BASE } from "./config";
import i18n from "../../i18n";
import {
  clearTokens,
  getAccessToken,
  getRefreshToken,
  isSafeRedirectPath,
  setRedirectPath,
  setTokens,
} from "./token";
import { clearAuthScopedCaches } from "./authCacheInvalidation";

let refreshPromise: Promise<string> | null = null;

export interface RefreshedTokens {
  access_token: string;
  refresh_token?: string;
}

function notifyLogout(): void {
  window.dispatchEvent(new CustomEvent("auth:logout"));
}

export function clearAuthState(): void {
  clearTokens();
  clearAuthScopedCaches();
  notifyLogout();
}

export function redirectToLogin(): void {
  if (typeof window !== "undefined") {
    const currentPath = window.location.pathname + window.location.search;
    if (isSafeRedirectPath(currentPath)) {
      setRedirectPath(currentPath);
    }
  }
  clearAuthState();
}

/**
 * Get a valid (non-expired) access token.
 *
 * Returns `null` when no token exists — the caller decides what to do.
 * Browser production auth is cookie-based, so no bearer token is returned.
 */
export async function getValidAccessToken(): Promise<string | null> {
  if (!getAccessToken()) {
    return null;
  }
  return null;
}

/**
 * Refresh tokens with deduplication to avoid concurrent refresh requests.
 *
 * Uses a ref-counted approach: the promise is cleared only after all
 * concurrent callers have awaited it, preventing race conditions where
 * a third caller starts a duplicate refresh.
 */
export async function refreshTokens(): Promise<RefreshedTokens> {
  if (refreshPromise) {
    const access_token = await refreshPromise;
    return {
      access_token,
      refresh_token: getRefreshToken() ?? undefined,
    };
  }

  refreshPromise = (async () => {
    if (!getRefreshToken()) {
      throw new Error("No browser session marker available");
    }

    const response = await fetch(`${API_BASE}/api/ai/auth/me`, {
      method: "GET",
      credentials: "include",
      headers: {
        "Accept-Language": i18n.language || "en",
      },
    });

    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        clearAuthState();
        throw new Error("Unauthorized");
      }
      throw new Error("Cookie session probe failed");
    }

    clearAuthScopedCaches();
    setTokens("cookie-session");
    return "cookie-session";
  })();

  try {
    const access_token = await refreshPromise;
    return {
      access_token,
      refresh_token: getRefreshToken() ?? undefined,
    };
  } finally {
    // Use microtask delay so that callers still awaiting the same promise
    // in the `if (refreshPromise)` branch finish before we clear it.
    Promise.resolve().then(() => {
      refreshPromise = null;
    });
  }
}

export async function refreshAccessToken(): Promise<string> {
  const { access_token } = await refreshTokens();
  return access_token;
}
