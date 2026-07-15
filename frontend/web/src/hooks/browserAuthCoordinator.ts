import { authApi } from "../services/api/auth";

export const BROWSER_AUTH_CONTEXT_NONCE_KEY =
  "ai_platform_auth_context_nonce_v1";
export const BROWSER_AUTH_CONTEXT_LOCK_NAME =
  "ai-platform-auth-context-bootstrap";

interface BrowserLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T>;
}

export class BrowserAuthCoordinatorError extends Error {
  constructor(readonly code: "auth_context_coordination_unavailable") {
    super(code);
    this.name = "BrowserAuthCoordinatorError";
  }
}

function browserStorage(): Storage | null {
  return typeof localStorage === "undefined" ? null : localStorage;
}

function existingNonce(storage: Storage | null): string | null {
  const nonce = storage?.getItem(BROWSER_AUTH_CONTEXT_NONCE_KEY) ?? null;
  return nonce && /^[A-Za-z0-9_-]{43,512}$/.test(nonce) ? nonce : null;
}

function createNonce(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) {
    throw new BrowserAuthCoordinatorError(
      "auth_context_coordination_unavailable",
    );
  }
  const bytes = cryptoApi.getRandomValues(new Uint8Array(32));
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join(
    "",
  );
}

function browserLocks(): BrowserLockManager | null {
  if (typeof navigator === "undefined") return null;
  return (
    (navigator as Navigator & { locks?: BrowserLockManager }).locks ?? null
  );
}

/**
 * Bootstrap the sole browser context under an origin-wide exclusive lock.
 *
 * The nonce is non-credential browser coordination data. The server derives
 * the opaque HttpOnly cookie handle and remains the auth-operation authority.
 */
export async function ensureBrowserAuthContext(
  signal?: AbortSignal,
): Promise<void> {
  const storage = browserStorage();
  const nonce = existingNonce(storage);
  if (nonce) {
    await authApi.bootstrapAuthContext(nonce, signal);
    return;
  }

  const locks = browserLocks();
  if (!storage || !locks) {
    throw new BrowserAuthCoordinatorError(
      "auth_context_coordination_unavailable",
    );
  }

  await locks.request(
    BROWSER_AUTH_CONTEXT_LOCK_NAME,
    { mode: "exclusive" },
    async () => {
      const lockedNonce = existingNonce(storage);
      const stableNonce = lockedNonce ?? createNonce();
      if (!lockedNonce) {
        storage.setItem(BROWSER_AUTH_CONTEXT_NONCE_KEY, stableNonce);
      }
      await authApi.bootstrapAuthContext(stableNonce, signal);
    },
  );
}
