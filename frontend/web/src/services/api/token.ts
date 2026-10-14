/**
 * Token management utilities
 */

export const AUTH_SESSION_MARKER_KEY = "ai_platform_session_present";
const TOKEN_KEY = "access_token";
const REFRESH_TOKEN_KEY = "refresh_token";
const REDIRECT_PATH_KEY = "redirect_after_login";

type StorageEventLike = {
  key: string | null;
  oldValue: string | null;
  newValue: string | null;
};

function browserLocalStorage(): Storage | null {
  return typeof localStorage === "undefined" ? null : localStorage;
}

function browserSessionStorage(): Storage | null {
  return typeof sessionStorage === "undefined" ? null : sessionStorage;
}

/** Explicit owner operation for removing obsolete script-readable credentials. */
export function migrateLegacyBearerStorage(): void {
  const storage = browserLocalStorage();
  if (!storage) {
    return;
  }
  if (typeof storage.getItem !== "function") {
    storage.removeItem?.(TOKEN_KEY);
    storage.removeItem?.(REFRESH_TOKEN_KEY);
    return;
  }
  if (storage.getItem(TOKEN_KEY) !== null) {
    storage.removeItem(TOKEN_KEY);
  }
  if (storage.getItem(REFRESH_TOKEN_KEY) !== null) {
    storage.removeItem(REFRESH_TOKEN_KEY);
  }
}

function createSessionMarker(): string | null {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) {
    return null;
  }
  return Array.from(
    cryptoApi.getRandomValues(new Uint8Array(32)),
    (value) => value.toString(16).padStart(2, "0"),
  ).join("");
}

export function isSafeRedirectPath(path: string): boolean {
  return path !== "/" && !path.startsWith("/auth/");
}

/**
 * 浏览器生产路径不再把脚本可读 bearer token 当作授权来源。
 * 这里仅返回一个非秘密的 cookie-session 存在标记，供 UI 判断是否需要探测服务端会话。
 */
export function getAccessToken(): string | null {
  return browserLocalStorage()?.getItem(AUTH_SESSION_MARKER_KEY) ?? null;
}

/**
 * 为了兼容旧调用方，这里返回与 access token 相同的非秘密会话标记。
 */
export function getRefreshToken(): string | null {
  return browserLocalStorage()?.getItem(AUTH_SESSION_MARKER_KEY) ?? null;
}

/**
 * 标记浏览器存在同源 cookie session，并顺带清掉旧 bearer 存储。
 */
export function setTokens(_access_token: string, _refresh_token?: string): void {
  const storage = browserLocalStorage();
  if (!storage) {
    return;
  }
  migrateLegacyBearerStorage();
  const marker = createSessionMarker();
  if (marker === null) {
    // A marker participates in browser ownership. Do not silently substitute
    // a predictable clock/Math.random value when secure browser entropy is
    // unavailable; callers fail closed by observing no local session marker.
    storage.removeItem(AUTH_SESSION_MARKER_KEY);
    return;
  }
  storage.setItem(AUTH_SESSION_MARKER_KEY, marker);
}

/**
 * 清除浏览器 cookie-session 标记以及历史 bearer 存储。
 */
export function clearTokens(): void {
  const storage = browserLocalStorage();
  if (!storage) {
    return;
  }
  storage.removeItem(AUTH_SESSION_MARKER_KEY);
  migrateLegacyBearerStorage();
}

/**
 * 获取登录后重定向路径
 */
export function getRedirectPath(): string | null {
  const redirectPath = browserSessionStorage()?.getItem(REDIRECT_PATH_KEY);
  if (!redirectPath) return null;

  if (!isSafeRedirectPath(redirectPath)) {
    browserSessionStorage()?.removeItem(REDIRECT_PATH_KEY);
    return null;
  }

  return redirectPath;
}

/**
 * 清除重定向路径
 */
export function clearRedirectPath(): void {
  browserSessionStorage()?.removeItem(REDIRECT_PATH_KEY);
}

export function setRedirectPath(path: string): void {
  browserSessionStorage()?.setItem(REDIRECT_PATH_KEY, path);
}

export function parseAuthStorageEvent(
  event: StorageEventLike,
): "login" | "replacement" | "logout" | null {
  if (event.key !== AUTH_SESSION_MARKER_KEY) {
    return null;
  }
  if (event.newValue && !event.oldValue) {
    return "login";
  }
  if (event.newValue && event.oldValue !== event.newValue) {
    return "replacement";
  }
  if (event.oldValue && !event.newValue) {
    return "logout";
  }
  return null;
}
