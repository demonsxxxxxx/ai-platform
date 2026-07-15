import { authApi } from "../services/api/auth";
import { ApiRequestError } from "../services/api/fetch";

export const BROWSER_AUTH_CONTEXT_NONCE_KEY =
  "ai_platform_auth_context_nonce_v1";
export const BROWSER_AUTH_CONTEXT_LOCK_NAME =
  "ai-platform-auth-context-bootstrap";
export const BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY =
  "auth-context-bootstrap";

const BROWSER_AUTH_CONTEXT_IDB_NAME = "ai-platform-auth-context";
const BROWSER_AUTH_CONTEXT_IDB_STORE = "coordination";
const BROWSER_AUTH_CONTEXT_IDB_VERSION = 1;
const BROWSER_AUTH_CONTEXT_LEASE_MS = 10_000;
const BROWSER_AUTH_CONTEXT_ACQUIRE_TIMEOUT_MS = 10_000;
const BROWSER_AUTH_CONTEXT_RETRY_MS = 25;

interface BrowserLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T>;
}

interface IndexedDbLeaseRecord {
  ownerToken: string | null;
  generation: number;
  leaseExpiresAt: number;
  pendingNonce: string | null;
  publishedNonce: string | null;
}

interface IndexedDbLease {
  ownerToken: string;
  generation: number;
  nonce: string;
}

type IndexedDbLeaseAcquisition =
  | { kind: "acquired"; lease: IndexedDbLease }
  | { kind: "waiting"; leaseExpiresAt: number };

export class BrowserAuthCoordinatorError extends Error {
  constructor(readonly code: "auth_context_coordination_unavailable") {
    super(code);
    this.name = "BrowserAuthCoordinatorError";
  }
}

function coordinationUnavailable(): BrowserAuthCoordinatorError {
  return new BrowserAuthCoordinatorError(
    "auth_context_coordination_unavailable",
  );
}

function browserStorage(): Storage | null {
  return typeof localStorage === "undefined" ? null : localStorage;
}

function existingNonce(storage: Storage | null): string | null {
  const nonce = storage?.getItem(BROWSER_AUTH_CONTEXT_NONCE_KEY) ?? null;
  return nonce && /^[A-Za-z0-9_-]{43,512}$/.test(nonce) ? nonce : null;
}

function isNonce(value: unknown): value is string {
  return typeof value === "string" && /^[A-Za-z0-9_-]{43,512}$/.test(value);
}

function createNonce(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) {
    throw coordinationUnavailable();
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

function browserIndexedDb(): IDBFactory | null {
  return typeof indexedDB === "undefined" ? null : indexedDB;
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

function isLeaseRecord(value: unknown): value is IndexedDbLeaseRecord {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const record = value as IndexedDbLeaseRecord;
  return (
    (isNonce(record.ownerToken) || record.ownerToken === null) &&
    Number.isSafeInteger(record.generation) &&
    record.generation >= 1 &&
    Number.isFinite(record.leaseExpiresAt) &&
    record.leaseExpiresAt >= 0 &&
    (isNonce(record.pendingNonce) || record.pendingNonce === null) &&
    (isNonce(record.publishedNonce) || record.publishedNonce === null) &&
    (record.pendingNonce !== null || record.publishedNonce !== null)
  );
}

function waitFor(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    throwIfAborted(signal);
    const timer = globalThis.setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    const abort = () => {
      globalThis.clearTimeout(timer);
      reject(
        signal?.reason ??
          new DOMException("Browser auth coordination aborted", "AbortError"),
      );
    };
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function withTimeout<T>(operation: Promise<T>, timeoutMs: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = globalThis.setTimeout(() => reject(coordinationUnavailable()), timeoutMs);
    operation.then(
      (value) => {
        globalThis.clearTimeout(timer);
        resolve(value);
      },
      () => {
        globalThis.clearTimeout(timer);
        reject(coordinationUnavailable());
      },
    );
  });
}

async function openCoordinatorDatabase(): Promise<IDBDatabase> {
  const factory = browserIndexedDb();
  if (!factory) throw coordinationUnavailable();

  try {
    const database = await withTimeout(
      new Promise<IDBDatabase>((resolve, reject) => {
        let request: IDBOpenDBRequest;
        try {
          request = factory.open(
            BROWSER_AUTH_CONTEXT_IDB_NAME,
            BROWSER_AUTH_CONTEXT_IDB_VERSION,
          );
        } catch {
          reject(coordinationUnavailable());
          return;
        }
        request.onupgradeneeded = () => {
          try {
            if (!request.result.objectStoreNames.contains(BROWSER_AUTH_CONTEXT_IDB_STORE)) {
              request.result.createObjectStore(BROWSER_AUTH_CONTEXT_IDB_STORE);
            }
          } catch {
            reject(coordinationUnavailable());
          }
        };
        request.onblocked = () => reject(coordinationUnavailable());
        request.onerror = () => reject(coordinationUnavailable());
        request.onsuccess = () => resolve(request.result);
      }),
      BROWSER_AUTH_CONTEXT_ACQUIRE_TIMEOUT_MS,
    );
    return database;
  } catch {
    throw coordinationUnavailable();
  }
}

function readwriteTransaction<T>(
  database: IDBDatabase,
  action: (
    store: IDBObjectStore,
    succeed: (value: T) => void,
    fail: () => void,
  ) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    let transaction: IDBTransaction;
    let result: T | undefined;
    let hasResult = false;
    let settled = false;
    const rejectUnavailable = () => {
      if (settled) return;
      settled = true;
      reject(coordinationUnavailable());
    };
    const fail = () => {
      if (settled) return;
      try {
        transaction.abort();
      } catch {
        rejectUnavailable();
      }
    };

    try {
      transaction = database.transaction(
        BROWSER_AUTH_CONTEXT_IDB_STORE,
        "readwrite",
      );
      transaction.onabort = rejectUnavailable;
      transaction.onerror = fail;
      transaction.oncomplete = () => {
        if (settled) return;
        if (!hasResult) {
          rejectUnavailable();
          return;
        }
        settled = true;
        resolve(result as T);
      };
      action(
        transaction.objectStore(BROWSER_AUTH_CONTEXT_IDB_STORE),
        (value) => {
          result = value;
          hasResult = true;
        },
        fail,
      );
    } catch {
      rejectUnavailable();
    }
  });
}

function failClosedHandler(callback: () => void, fail: () => void): () => void {
  return () => {
    try {
      callback();
    } catch {
      fail();
    }
  };
}

async function acquireIndexedDbLease(
  database: IDBDatabase,
): Promise<IndexedDbLeaseAcquisition> {
  const ownerToken = createNonce();
  const freshNonce = createNonce();
  return readwriteTransaction(database, (store, succeed, fail) => {
    const read = store.get(BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY);
    read.onerror = fail;
    read.onsuccess = failClosedHandler(() => {
      const current = read.result;
      if (current !== undefined && !isLeaseRecord(current)) {
        fail();
        return;
      }
      const record = current as IndexedDbLeaseRecord | undefined;
      const now = Date.now();
      if (
        record?.ownerToken !== null &&
        record?.ownerToken !== undefined &&
        record.leaseExpiresAt > now
      ) {
        succeed({ kind: "waiting", leaseExpiresAt: record.leaseExpiresAt });
        return;
      }

      const nonce = record?.pendingNonce ?? record?.publishedNonce ?? freshNonce;
      const next: IndexedDbLeaseRecord = {
        ownerToken,
        generation: (record?.generation ?? 0) + 1,
        leaseExpiresAt: now + BROWSER_AUTH_CONTEXT_LEASE_MS,
        pendingNonce: nonce,
        publishedNonce: record?.publishedNonce ?? null,
      };
      const write = store.put(next, BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY);
      write.onerror = fail;
      write.onsuccess = failClosedHandler(() => {
        succeed({
          kind: "acquired",
          lease: {
            ownerToken,
            generation: next.generation,
            nonce,
          },
        });
      }, fail);
    }, fail);
  });
}

async function rotateIndexedDbLease(
  database: IDBDatabase,
  lease: IndexedDbLease,
): Promise<IndexedDbLease | null> {
  const rotatedNonce = createNonce();
  return readwriteTransaction(database, (store, succeed, fail) => {
    const read = store.get(BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY);
    read.onerror = fail;
    read.onsuccess = failClosedHandler(() => {
      if (!isLeaseRecord(read.result)) {
        fail();
        return;
      }
      const current = read.result;
      if (
        current.ownerToken !== lease.ownerToken ||
        current.generation !== lease.generation ||
        current.leaseExpiresAt <= Date.now()
      ) {
        succeed(null);
        return;
      }

      const next: IndexedDbLeaseRecord = {
        ...current,
        generation: current.generation + 1,
        leaseExpiresAt: Date.now() + BROWSER_AUTH_CONTEXT_LEASE_MS,
        pendingNonce: rotatedNonce,
      };
      const write = store.put(next, BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY);
      write.onerror = fail;
      write.onsuccess = failClosedHandler(() => {
        succeed({
          ownerToken: lease.ownerToken,
          generation: next.generation,
          nonce: rotatedNonce,
        });
      }, fail);
    }, fail);
  });
}

async function publishIndexedDbLease(
  database: IDBDatabase,
  lease: IndexedDbLease,
): Promise<boolean> {
  return readwriteTransaction(database, (store, succeed, fail) => {
    const read = store.get(BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY);
    read.onerror = fail;
    read.onsuccess = failClosedHandler(() => {
      if (!isLeaseRecord(read.result)) {
        fail();
        return;
      }
      const current = read.result;
      if (
        current.ownerToken !== lease.ownerToken ||
        current.generation !== lease.generation ||
        current.leaseExpiresAt <= Date.now() ||
        current.pendingNonce !== lease.nonce
      ) {
        succeed(false);
        return;
      }
      const write = store.put(
        {
          ...current,
          pendingNonce: null,
          publishedNonce: lease.nonce,
        } satisfies IndexedDbLeaseRecord,
        BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY,
      );
      write.onerror = fail;
      write.onsuccess = failClosedHandler(() => succeed(true), fail);
    }, fail);
  });
}

async function releaseIndexedDbLease(
  database: IDBDatabase,
  lease: IndexedDbLease,
): Promise<boolean> {
  return readwriteTransaction(database, (store, succeed, fail) => {
    const read = store.get(BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY);
    read.onerror = fail;
    read.onsuccess = failClosedHandler(() => {
      if (!isLeaseRecord(read.result)) {
        fail();
        return;
      }
      const current = read.result;
      if (
        current.ownerToken !== lease.ownerToken ||
        current.generation !== lease.generation ||
        current.leaseExpiresAt <= Date.now()
      ) {
        succeed(false);
        return;
      }
      const write = store.put(
        {
          ...current,
          ownerToken: null,
          leaseExpiresAt: 0,
        } satisfies IndexedDbLeaseRecord,
        BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY,
      );
      write.onerror = fail;
      write.onsuccess = failClosedHandler(() => succeed(true), fail);
    }, fail);
  });
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

async function bootstrapUnderIndexedDbLease(
  database: IDBDatabase,
  acquiredLease: IndexedDbLease,
  signal?: AbortSignal,
): Promise<"completed" | "lost"> {
  let lease = acquiredLease;
  try {
    try {
      // Match the Web Locks path: once the request begins, it is deliberately
      // not connected to caller cancellation.
      await authApi.bootstrapAuthContext(lease.nonce);
    } catch (error) {
      if (!isRebootstrapRequired(error)) throw error;

      throwIfAborted(signal);
      const rotated = await rotateIndexedDbLease(database, lease);
      if (!rotated) return "lost";
      lease = rotated;
      await authApi.bootstrapAuthContext(lease.nonce);
    }

    if (!await publishIndexedDbLease(database, lease)) return "lost";
    await releaseIndexedDbLease(database, lease);
    return "completed";
  } catch (error) {
    try {
      await releaseIndexedDbLease(database, lease);
    } catch {
      // The original bootstrap outcome remains authoritative for this caller.
    }
    throw error;
  }
}

async function ensureIndexedDbBrowserAuthContext(
  signal?: AbortSignal,
): Promise<void> {
  const database = await openCoordinatorDatabase();
  const deadline = Date.now() + BROWSER_AUTH_CONTEXT_ACQUIRE_TIMEOUT_MS;
  try {
    while (Date.now() <= deadline) {
      throwIfAborted(signal);
      const acquisition = await acquireIndexedDbLease(database);
      if (acquisition.kind === "acquired") {
        const outcome = await bootstrapUnderIndexedDbLease(
          database,
          acquisition.lease,
          signal,
        );
        if (outcome === "completed") return;
        continue;
      }

      const remaining = deadline - Date.now();
      if (remaining <= 0) break;
      await waitFor(
        Math.min(
          remaining,
          BROWSER_AUTH_CONTEXT_RETRY_MS,
          Math.max(1, acquisition.leaseExpiresAt - Date.now()),
        ),
        signal,
      );
    }
  } finally {
    if (typeof database.close === "function") database.close();
  }
  throw coordinationUnavailable();
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
  const locks = browserLocks();
  if (!locks) {
    await ensureIndexedDbBrowserAuthContext(signal);
    return;
  }

  const storage = browserStorage();
  if (!storage) throw coordinationUnavailable();
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
