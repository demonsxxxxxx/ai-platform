import { authApi } from "../services/api/auth";
import { ApiRequestError } from "../services/api/fetch";

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

function isRebootstrapRequired(error: unknown): boolean {
  return (
    error instanceof ApiRequestError &&
    error.code === "auth_context_rebootstrap_required"
  );
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw signal.reason ?? new DOMException("Browser auth coordination aborted", "AbortError");
  }
}

async function bootstrapAndPublishNonce(
  storage: Storage,
  nonce: string,
  signal?: AbortSignal,
): Promise<void> {
  try {
    // Once started, this short request must complete before any nonce is
    // published. Caller cancellation is handled before a request starts.
    await authApi.bootstrapAuthContext(nonce);
  } catch (error) {
    if (!isRebootstrapRequired(error)) throw error;

    throwIfAborted(signal);
    const rotatedNonce = createNonce();
    await authApi.bootstrapAuthContext(rotatedNonce);
    storage.setItem(BROWSER_AUTH_CONTEXT_NONCE_KEY, rotatedNonce);
    return;
  }

  storage.setItem(BROWSER_AUTH_CONTEXT_NONCE_KEY, nonce);
}

/**
 * Bootstrap the sole browser context under an origin-wide exclusive lock.
 *
 * The nonce is non-credential browser coordination data. The server derives
 * the opaque HttpOnly cookie handle and remains the auth-operation authority.
 *
 * Cancellation is honored before acquiring or entering the lock and again
 * immediately before rotating after a rebootstrap response. Once a bootstrap
 * request starts, its result is completed and the successful nonce is
 * published before this function returns; callers fence stale ownership after
 * awaiting this coordinator instead of aborting the request mid-commit.
 */
export async function ensureBrowserAuthContext(
  signal?: AbortSignal,
): Promise<void> {
  throwIfAborted(signal);
  const storage = browserStorage();
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
      throwIfAborted(signal);
      const stableNonce = existingNonce(storage) ?? createNonce();
      await bootstrapAndPublishNonce(storage, stableNonce, signal);
    },
  );
}
