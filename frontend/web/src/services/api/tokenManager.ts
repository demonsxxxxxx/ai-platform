import { API_BASE } from "./config";
import { getAccessToken } from "./token";
import { ApiProtocolError, apiRequestErrorFromResponse } from "./fetch";
import type { PrincipalResponseWire } from "./auth";

export const SESSION_REFRESHED_EVENT = "auth:session-refreshed";

export interface RefreshedTokens {
  /** Non-secret ownership marker; browser credentials remain HttpOnly. */
  access_token: string;
  principal: PrincipalResponseWire;
  authorization_changed: boolean;
}

export class AuthSessionChangedError extends Error {
  constructor() {
    super("auth_session_changed");
    this.name = "AuthSessionChangedError";
  }
}

let refreshPromise: Promise<RefreshedTokens> | null = null;
let refreshMarker: string | null = null;

/** Browser requests authenticate with cookies, never a script-readable bearer. */
export async function getValidAccessToken(): Promise<string | null> {
  return null;
}

function assertCurrentMarker(marker: string): void {
  if (getAccessToken() !== marker) throw new AuthSessionChangedError();
}

function waitForRefresh<T>(pending: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return pending;
  signal.throwIfAborted();
  return new Promise<T>((resolve, reject) => {
    const abort = () => reject(signal.reason);
    signal.addEventListener("abort", abort, { once: true });
    pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", abort));
  });
}

/** Merge refreshes for one identity without letting an aborted caller cancel peers. */
export async function refreshTokens(signal?: AbortSignal): Promise<RefreshedTokens> {
  signal?.throwIfAborted();
  const marker = getAccessToken();
  if (!marker) throw new AuthSessionChangedError();
  if (!refreshPromise || refreshMarker !== marker) {
    refreshMarker = marker;
    const pending = (async () => {
      const response = await fetch(`${API_BASE}/api/ai/auth/refresh`, {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "Accept-Language": "zh-CN" },
        body: "{}",
        signal: AbortSignal.timeout(15_000),
      });
      assertCurrentMarker(marker);
      if (!response.ok) {
        const error = await apiRequestErrorFromResponse(response);
        assertCurrentMarker(marker);
        throw error;
      }
      let payload: unknown;
      try {
        payload = JSON.parse(await response.text());
      } catch (error) {
        if (error instanceof SyntaxError) throw new ApiProtocolError(response.status);
        throw error;
      }
      assertCurrentMarker(marker);
      const result = payload as Partial<RefreshedTokens> | null;
      const principal = result?.principal;
      if (!principal || typeof principal.user_id !== "string" || !principal.user_id
        || typeof principal.tenant_id !== "string" || !principal.tenant_id
        || typeof principal.display_name !== "string"
        || !Array.isArray(principal.roles) || !principal.roles.every((role) => typeof role === "string")
        || !Array.isArray(principal.permissions) || !principal.permissions.every((permission) => typeof permission === "string")
        || typeof result?.authorization_changed !== "boolean") {
        throw new ApiProtocolError(response.status);
      }
      if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
        const accepted = window.dispatchEvent(new CustomEvent(SESSION_REFRESHED_EVENT, {
          cancelable: true,
          detail: { principal, authorization_changed: result.authorization_changed, request_marker: marker },
        }));
        if (!accepted) throw new AuthSessionChangedError();
      }
      assertCurrentMarker(marker);
      return {
        access_token: marker,
        principal,
        authorization_changed: result.authorization_changed,
      };
    })().finally(() => {
      if (refreshPromise === pending) {
        refreshPromise = null;
        refreshMarker = null;
      }
    });
    refreshPromise = pending;
  }
  const result = await waitForRefresh(refreshPromise, signal);
  signal?.throwIfAborted();
  assertCurrentMarker(marker);
  return result;
}

/** SSE shares the same cookie refresh and receives only the ownership marker. */
export async function refreshAccessToken(signal?: AbortSignal): Promise<string> {
  return (await refreshTokens(signal)).access_token;
}
