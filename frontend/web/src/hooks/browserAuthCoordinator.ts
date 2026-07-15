import {
  authApi,
  type AuthContextBootstrapResponse,
} from "../services/api/auth";
import { ApiRequestError } from "../services/api/fetch";

export const BROWSER_AUTH_CONTEXT_NONCE_KEY =
  "ai_platform_auth_context_nonce_v1";
export const BROWSER_AUTH_CONTEXT_LOCK_NAME =
  "ai-platform-auth-context-bootstrap";
export const BROWSER_AUTH_CONTEXT_V2_DB_NAME =
  "ai-platform-browser-auth-context-v2";
export const BROWSER_AUTH_CONTEXT_V2_STORE_NAME = "coordination";

const BROWSER_AUTH_CONTEXT_V2_RECORD_KEY = "current";
const BROWSER_AUTH_CONTEXT_V2_DB_VERSION = 1;
const ACQUISITION_TIMEOUT_MS = 10_000;
const OWNER_LEASE_MS = 5_000;
const ACQUISITION_RETRY_MS = 25;
const BASE64URL_RE = /^[A-Za-z0-9_-]{43}$/;

interface BrowserLockManager {
  request<T>(
    name: string,
    options: { mode: "exclusive" },
    callback: () => Promise<T>,
  ): Promise<T>;
}

interface PendingRotation {
  baseGeneration: number;
  nextNonce: string;
  ticket: string;
}

interface BrowserAuthV2State {
  id: typeof BROWSER_AUTH_CONTEXT_V2_RECORD_KEY;
  version: 2;
  incarnation: string;
  currentGeneration: number;
  currentNonce: string;
  confirmedGeneration: number;
  pendingRotation?: PendingRotation;
  ownerToken: string;
  leaseExpiresAt: number;
}

interface OwnedV2State {
  state: BrowserAuthV2State;
  ownerToken: string;
}

interface StateMutation<T> {
  result: T;
  next?: BrowserAuthV2State;
}

interface CoordinatorDbSession {
  readonly liveTransactions: Set<IDBTransaction>;
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

function base64url(bytes: Uint8Array): string {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
  let encoded = "";
  for (let index = 0; index + 2 < bytes.length; index += 3) {
    const value = (bytes[index] << 16) | (bytes[index + 1] << 8) | bytes[index + 2];
    encoded += alphabet[(value >>> 18) & 63];
    encoded += alphabet[(value >>> 12) & 63];
    encoded += alphabet[(value >>> 6) & 63];
    encoded += alphabet[value & 63];
  }
  const remaining = bytes.length % 3;
  if (remaining === 1) {
    const value = bytes[bytes.length - 1];
    encoded += alphabet[(value >>> 2) & 63];
    encoded += alphabet[(value & 3) << 4];
  } else if (remaining === 2) {
    const value = (bytes[bytes.length - 2] << 8) | bytes[bytes.length - 1];
    encoded += alphabet[(value >>> 10) & 63];
    encoded += alphabet[(value >>> 4) & 63];
    encoded += alphabet[(value & 15) << 2];
  }
  return encoded;
}

function createV2Random(): string {
  const cryptoApi = globalThis.crypto;
  if (!cryptoApi?.getRandomValues) {
    throw new BrowserAuthCoordinatorError(
      "auth_context_coordination_unavailable",
    );
  }
  return base64url(cryptoApi.getRandomValues(new Uint8Array(32)));
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

function isAuthContextStale(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === "auth_context_stale";
}

function isUnknownRotationResult(error: unknown): boolean {
  return error instanceof ApiRequestError && error.code === "auth_context_unavailable";
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) {
    throw signal.reason ?? new DOMException("Browser auth coordination aborted", "AbortError");
  }
}

function unavailable(): BrowserAuthCoordinatorError {
  return new BrowserAuthCoordinatorError("auth_context_coordination_unavailable");
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function isCurrentState(value: unknown): value is BrowserAuthV2State {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const state = value as Partial<BrowserAuthV2State>;
  const pending = state.pendingRotation;
  const validGeneration = Number.isSafeInteger(state.currentGeneration)
    && (state.currentGeneration ?? 0) >= 1;
  const validConfirmation = Number.isSafeInteger(state.confirmedGeneration)
    && (state.confirmedGeneration ?? -1) >= 0
    && (state.confirmedGeneration ?? 0) <= (state.currentGeneration ?? 0);
  const validLease = typeof state.leaseExpiresAt === "number"
    && Number.isFinite(state.leaseExpiresAt)
    && state.leaseExpiresAt >= 0;
  const validPending = pending === undefined || (
    typeof pending === "object"
    && pending !== null
    && Number.isSafeInteger(pending.baseGeneration)
    && pending.baseGeneration >= 1
    && /^[A-Za-z0-9_-]{43,512}$/.test(pending.nextNonce)
    && BASE64URL_RE.test(pending.ticket)
  );
  return (
    state.id === BROWSER_AUTH_CONTEXT_V2_RECORD_KEY
    && state.version === 2
    && BASE64URL_RE.test(state.incarnation ?? "")
    && validGeneration
    && validConfirmation
    && /^[A-Za-z0-9_-]{43,512}$/.test(state.currentNonce ?? "")
    && (state.ownerToken === "" || BASE64URL_RE.test(state.ownerToken ?? ""))
    && validLease
    && validPending
  );
}

function cloneState(state: BrowserAuthV2State): BrowserAuthV2State {
  return {
    ...state,
    pendingRotation: state.pendingRotation
      ? { ...state.pendingRotation }
      : undefined,
  };
}

function sameCurrentState(
  state: BrowserAuthV2State,
  expected: BrowserAuthV2State,
): boolean {
  return state.incarnation === expected.incarnation
    && state.currentGeneration === expected.currentGeneration
    && state.currentNonce === expected.currentNonce;
}

function deadlineRemaining(deadline: number): number {
  return deadline - Date.now();
}

function openCoordinatorDb(signal: AbortSignal | undefined, deadline: number): Promise<IDBDatabase> {
  const factory = browserIndexedDb();
  if (!factory || deadlineRemaining(deadline) <= 0) {
    return Promise.reject(unavailable());
  }
  try {
    throwIfAborted(signal);
  } catch (error) {
    return Promise.reject(error);
  }
  return new Promise((resolve, reject) => {
    let settled = false;
    let request: IDBOpenDBRequest;
    const finish = (error?: unknown, db?: IDBDatabase) => {
      if (settled) {
        db?.close();
        return;
      }
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortOpen);
      if (error !== undefined) {
        // A blocked/timed-out open can later acquire the version-change lock.
        // Keep an upgrade handler solely to abort that late transaction before
        // it can create the coordinator store or otherwise mutate schema.
        request.onupgradeneeded = () => {
          try {
            request.transaction?.abort();
          } catch {
            // The transaction may already be settled; no schema work follows.
          }
        };
        request.onblocked = null;
        request.onerror = null;
        // An open request cannot be cancelled after a blocked upgrade. Keep
        // exactly one late-success closer, then remove all handlers after it.
        request.onsuccess = () => {
          request.result.close();
          request.onupgradeneeded = null;
          request.onblocked = null;
          request.onerror = null;
          request.onsuccess = null;
        };
        try {
          request.transaction?.abort();
        } catch {
          // The request may already have completed; a later success is closed.
        }
        reject(error);
      } else if (db) {
        request.onupgradeneeded = null;
        request.onblocked = null;
        request.onerror = null;
        request.onsuccess = null;
        resolve(db);
      } else {
        reject(unavailable());
      }
    };
    const abortOpen = () => finish(signal?.reason ?? new DOMException("Browser auth coordination aborted", "AbortError"));
    const timer = setTimeout(() => finish(unavailable()), Math.max(1, deadlineRemaining(deadline)));
    try {
      request = factory.open(BROWSER_AUTH_CONTEXT_V2_DB_NAME, BROWSER_AUTH_CONTEXT_V2_DB_VERSION);
    } catch {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortOpen);
      reject(unavailable());
      return;
    }
    signal?.addEventListener("abort", abortOpen, { once: true });
    request.onupgradeneeded = () => {
      if (settled) {
        try {
          request.transaction?.abort();
        } catch {
          // A late versionchange must not create or mutate the schema.
        }
        return;
      }
      try {
        if (!request.result.objectStoreNames.contains(BROWSER_AUTH_CONTEXT_V2_STORE_NAME)) {
          request.result.createObjectStore(BROWSER_AUTH_CONTEXT_V2_STORE_NAME, { keyPath: "id" });
        }
      } catch {
        finish(unavailable());
      }
    };
    request.onblocked = () => finish(unavailable());
    request.onerror = () => finish(unavailable());
    request.onsuccess = () => finish(undefined, request.result);
  });
}

function mutateCoordinatorState<T>(
  database: IDBDatabase,
  signal: AbortSignal | undefined,
  deadline: number,
  session: CoordinatorDbSession,
  mutation: (state: BrowserAuthV2State | null) => StateMutation<T>,
): Promise<T> {
  if (deadlineRemaining(deadline) <= 0) return Promise.reject(unavailable());
  try {
    throwIfAborted(signal);
  } catch (error) {
    return Promise.reject(error);
  }
  return new Promise((resolve, reject) => {
    let transaction: IDBTransaction;
    try {
      transaction = database.transaction(BROWSER_AUTH_CONTEXT_V2_STORE_NAME, "readwrite");
    } catch {
      reject(unavailable());
      return;
    }
    session.liveTransactions.add(transaction);
    let settled = false;
    let forcedError: unknown;
    let result: T;
    const finish = (error?: unknown) => {
      if (settled) return;
      settled = true;
      session.liveTransactions.delete(transaction);
      clearTimeout(timer);
      signal?.removeEventListener("abort", abortTransaction);
      if (error !== undefined) reject(error);
      else resolve(result);
    };
    const abortTransaction = () => {
      forcedError = signal?.reason ?? new DOMException("Browser auth coordination aborted", "AbortError");
      try {
        transaction.abort();
      } catch {
        finish(forcedError);
      }
    };
    const timeout = () => {
      forcedError = unavailable();
      try {
        transaction.abort();
      } catch {
        finish(forcedError);
      }
    };
    const timer = setTimeout(timeout, Math.max(1, deadlineRemaining(deadline)));
    signal?.addEventListener("abort", abortTransaction, { once: true });
    transaction.onabort = () => finish(forcedError ?? unavailable());
    transaction.onerror = () => finish(forcedError ?? unavailable());
    transaction.oncomplete = () => finish();
    let request: IDBRequest<unknown>;
    try {
      request = transaction.objectStore(BROWSER_AUTH_CONTEXT_V2_STORE_NAME)
        .get(BROWSER_AUTH_CONTEXT_V2_RECORD_KEY);
    } catch {
      forcedError = unavailable();
      try {
        transaction.abort();
      } catch {
        finish(forcedError);
      }
      return;
    }
    request.onerror = () => {
      forcedError = unavailable();
      try {
        transaction.abort();
      } catch {
        finish(forcedError);
      }
    };
    request.onsuccess = () => {
      if (settled || signal?.aborted || deadlineRemaining(deadline) <= 0) {
        timeout();
        return;
      }
      const raw = request.result;
      if (raw !== undefined && raw !== null && !isCurrentState(raw)) {
        forcedError = unavailable();
        try {
          transaction.abort();
        } catch {
          finish(forcedError);
        }
        return;
      }
      try {
        const outcome = mutation(raw ? cloneState(raw) : null);
        result = outcome.result;
        if (outcome.next) {
          transaction.objectStore(BROWSER_AUTH_CONTEXT_V2_STORE_NAME).put(outcome.next);
        }
      } catch (error) {
        forcedError = error;
        try {
          transaction.abort();
        } catch {
          finish(error);
        }
      }
    };
  });
}

async function withCoordinatorDb<T>(
  signal: AbortSignal | undefined,
  deadline: number,
  mutation: (state: BrowserAuthV2State | null) => StateMutation<T>,
): Promise<T> {
  const database = await openCoordinatorDb(signal, deadline);
  const session: CoordinatorDbSession = { liveTransactions: new Set() };
  const abortLiveTransactions = () => {
    for (const transaction of [...session.liveTransactions]) {
      try {
        transaction.abort();
      } catch {
        // A completed transaction is removed by its terminal event handler.
      }
    }
  };
  database.onversionchange = () => {
    abortLiveTransactions();
    database.close();
  };
  try {
    return await mutateCoordinatorState(database, signal, deadline, session, mutation);
  } finally {
    abortLiveTransactions();
    database.onversionchange = null;
    database.close();
  }
}

function createInitialV2State(): BrowserAuthV2State {
  return {
    id: BROWSER_AUTH_CONTEXT_V2_RECORD_KEY,
    version: 2,
    incarnation: createV2Random(),
    currentGeneration: 1,
    // Import only a syntactically valid V1 nonce. This enables matching V1 to
    // V2 migration and never treats an arbitrary localStorage value as state.
    currentNonce: existingNonce(browserStorage()) ?? createNonce(),
    confirmedGeneration: 0,
    ownerToken: "",
    leaseExpiresAt: 0,
  };
}

function waitForRetry(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
    };
    const abort = () => {
      cleanup();
      reject(signal?.reason ?? new DOMException("Browser auth coordination aborted", "AbortError"));
    };
    const complete = () => {
      cleanup();
      resolve();
    };
    const timer = setTimeout(complete, milliseconds);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

async function acquireV2Owner(signal?: AbortSignal): Promise<OwnedV2State> {
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  const ownerToken = createV2Random();
  while (deadlineRemaining(deadline) > 0) {
    throwIfAborted(signal);
    const outcome = await withCoordinatorDb(signal, deadline, (stored) => {
      const state = stored ?? createInitialV2State();
      const now = Date.now();
      if (state.ownerToken && state.ownerToken !== ownerToken && state.leaseExpiresAt > now) {
        return { result: null };
      }
      state.ownerToken = ownerToken;
      state.leaseExpiresAt = now + OWNER_LEASE_MS;
      return { result: cloneState(state), next: state };
    });
    if (outcome) return { state: outcome, ownerToken };
    await waitForRetry(Math.min(ACQUISITION_RETRY_MS, Math.max(1, deadlineRemaining(deadline))), signal);
  }
  throw unavailable();
}

function ownerMatches(state: BrowserAuthV2State, owned: OwnedV2State): boolean {
  return state.ownerToken === owned.ownerToken
    && state.leaseExpiresAt > Date.now()
    && sameCurrentState(state, owned.state);
}

async function assertV2Owner(owned: OwnedV2State, signal?: AbortSignal): Promise<BrowserAuthV2State> {
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  const current = await withCoordinatorDb(signal, deadline, (state) => {
    if (!state || !ownerMatches(state, owned)) throw unavailable();
    return { result: cloneState(state) };
  });
  return current;
}

async function releaseV2Owner(owned: OwnedV2State): Promise<boolean> {
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  try {
    return await withCoordinatorDb(undefined, deadline, (state) => {
      if (!state || !ownerMatches(state, owned)) return { result: false };
      state.ownerToken = "";
      state.leaseExpiresAt = 0;
      return { result: true, next: state };
    });
  } catch {
    return false;
  }
}

async function persistPendingRotation(
  owned: OwnedV2State,
  ticket: string,
  signal?: AbortSignal,
): Promise<OwnedV2State> {
  if (!BASE64URL_RE.test(ticket)) throw unavailable();
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  const next = await withCoordinatorDb(signal, deadline, (state) => {
    if (!state || !ownerMatches(state, owned)) throw unavailable();
    const pendingRotation: PendingRotation = {
      baseGeneration: state.currentGeneration,
      nextNonce: createNonce(),
      ticket,
    };
    state.pendingRotation = pendingRotation;
    return { result: cloneState(state), next: state };
  });
  return { state: next, ownerToken: owned.ownerToken };
}

async function replacePendingRotationTicket(
  owned: OwnedV2State,
  pending: PendingRotation,
  ticket: string,
  signal?: AbortSignal,
): Promise<OwnedV2State> {
  if (!BASE64URL_RE.test(ticket)) throw unavailable();
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  const next = await withCoordinatorDb(signal, deadline, (state) => {
    if (
      !state
      || !ownerMatches(state, owned)
      || !state.pendingRotation
      || state.pendingRotation.baseGeneration !== pending.baseGeneration
      || state.pendingRotation.nextNonce !== pending.nextNonce
      || state.pendingRotation.ticket !== pending.ticket
    ) {
      throw unavailable();
    }
    state.pendingRotation = { ...state.pendingRotation, ticket };
    return { result: cloneState(state), next: state };
  });
  return { state: next, ownerToken: owned.ownerToken };
}

function pendingRotationMatches(
  state: BrowserAuthV2State,
  pending: PendingRotation,
): boolean {
  return state.pendingRotation?.baseGeneration === pending.baseGeneration
    && state.pendingRotation.nextNonce === pending.nextNonce
    && state.pendingRotation.ticket === pending.ticket;
}

async function assertPendingV2Owner(
  owned: OwnedV2State,
  pending: PendingRotation,
  signal?: AbortSignal,
): Promise<OwnedV2State> {
  const current = await assertV2Owner(owned, signal);
  if (!pendingRotationMatches(current, pending)) throw unavailable();
  return { state: current, ownerToken: owned.ownerToken };
}

async function completeV2Rotation(
  owned: OwnedV2State,
  pending: PendingRotation,
  signal?: AbortSignal,
): Promise<void> {
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  await withCoordinatorDb(signal, deadline, (state) => {
    if (
      !state
      || !ownerMatches(state, owned)
      || !state.pendingRotation
      || state.pendingRotation.baseGeneration !== pending.baseGeneration
      || state.pendingRotation.nextNonce !== pending.nextNonce
      || state.pendingRotation.ticket !== pending.ticket
    ) {
      throw unavailable();
    }
    state.currentGeneration = pending.baseGeneration + 1;
    state.currentNonce = pending.nextNonce;
    state.confirmedGeneration = state.currentGeneration;
    state.pendingRotation = undefined;
    state.ownerToken = "";
    state.leaseExpiresAt = 0;
    return { result: undefined, next: state };
  });
}

async function confirmAndReleaseV2Owner(
  owned: OwnedV2State,
  signal?: AbortSignal,
): Promise<void> {
  const deadline = Date.now() + ACQUISITION_TIMEOUT_MS;
  await withCoordinatorDb(signal, deadline, (state) => {
    if (!state || !ownerMatches(state, owned)) throw unavailable();
    state.confirmedGeneration = state.currentGeneration;
    state.ownerToken = "";
    state.leaseExpiresAt = 0;
    return { result: undefined, next: state };
  });
}

function assertV2Ready(
  response: AuthContextBootstrapResponse | void,
  generation: number,
): void {
  if (
    !response
    || response.status !== "ready"
    || response.protocol_version !== 2
    || response.generation !== generation
  ) {
    throw unavailable();
  }
}

async function rotateV2Owner(
  owned: OwnedV2State,
  signal?: AbortSignal,
  allowTargetRepair = true,
  allowTicketReissue = true,
): Promise<void> {
  const current = await assertV2Owner(owned, signal);
  const pending = current.pendingRotation;
  if (!pending || pending.baseGeneration !== current.currentGeneration) {
    throw unavailable();
  }
  let response: AuthContextBootstrapResponse | void;
  try {
    response = await authApi.bootstrapAuthContext(
      {
        nonce: pending.nextNonce,
        protocol_version: 2,
        browser_incarnation: current.incarnation,
        generation: pending.baseGeneration + 1,
        rotation_ticket: pending.ticket,
      },
      signal,
    );
  } catch (error) {
    if (!allowTargetRepair || (!isAuthContextStale(error) && !isUnknownRotationResult(error))) {
      throw error;
    }
    // A ticketed request may have committed Redis while its response or cookie
    // write was lost. Try exactly one no-ticket target repair before deciding
    // that the authority is still at base and a fresh ticket is appropriate.
    const afterTicketFailure = await assertPendingV2Owner(
      { state: current, ownerToken: owned.ownerToken },
      pending,
      signal,
    );
    try {
      const repaired = await authApi.bootstrapAuthContext(
        {
          nonce: pending.nextNonce,
          protocol_version: 2,
          browser_incarnation: afterTicketFailure.state.incarnation,
          generation: pending.baseGeneration + 1,
        },
        signal,
      );
      const afterRepair = await assertPendingV2Owner(
        afterTicketFailure,
        pending,
        signal,
      );
      assertV2Ready(repaired, pending.baseGeneration + 1);
      await completeV2Rotation(afterRepair, pending, signal);
      return;
    } catch (repairError) {
      if (!allowTicketReissue || !isRebootstrapRequired(repairError)) {
        throw repairError;
      }
    }

    // The target-repair Lua branch proved authority remains at base. One
    // base-generation reissue is bounded to this recovery attempt; replacing
    // the ticket itself rechecks owner/base/nonce atomically in IndexedDB.
    const beforeReissue = await assertPendingV2Owner(
      { state: current, ownerToken: owned.ownerToken },
      pending,
      signal,
    );
    const renewal = await authApi.bootstrapAuthContext(
      {
        nonce: pending.nextNonce,
        protocol_version: 2,
        browser_incarnation: beforeReissue.state.incarnation,
        generation: pending.baseGeneration,
      },
      signal,
    );
    if (
      !renewal
      || renewal.status !== "rebootstrap_required"
      || renewal.protocol_version !== 2
      || renewal.generation !== pending.baseGeneration
      || !BASE64URL_RE.test(renewal.rotation_ticket)
    ) {
      throw unavailable();
    }
    const renewed = await replacePendingRotationTicket(
      beforeReissue,
      pending,
      renewal.rotation_ticket,
      signal,
    );
    await rotateV2Owner(renewed, signal, false, false);
    return;
  }
  const afterTicketResponse = await assertPendingV2Owner(
    { state: current, ownerToken: owned.ownerToken },
    pending,
    signal,
  );
  assertV2Ready(response, pending.baseGeneration + 1);
  await completeV2Rotation(
    afterTicketResponse,
    pending,
    signal,
  );
}

async function ensureV2BrowserAuthContext(
  signal?: AbortSignal,
  forceBootstrap = false,
): Promise<void> {
  let owned: OwnedV2State | null = null;
  try {
    owned = await acquireV2Owner(signal);
    const current = await assertV2Owner(owned, signal);
    if (current.pendingRotation) {
      await rotateV2Owner({ state: current, ownerToken: owned.ownerToken }, signal);
      return;
    }
    if (!forceBootstrap && current.confirmedGeneration === current.currentGeneration) {
      const released = await releaseV2Owner({ state: current, ownerToken: owned.ownerToken });
      if (!released) throw unavailable();
      return;
    }
    const response = await authApi.bootstrapAuthContext(
      {
        nonce: current.currentNonce,
        protocol_version: 2,
        browser_incarnation: current.incarnation,
        generation: current.currentGeneration,
      },
      signal,
    );
    if (response?.status === "rebootstrap_required") {
      const pendingOwner = await persistPendingRotation(
        { state: current, ownerToken: owned.ownerToken },
        response.rotation_ticket,
        signal,
      );
      await rotateV2Owner(pendingOwner, signal);
      return;
    }
    assertV2Ready(response, current.currentGeneration);
    await confirmAndReleaseV2Owner(
      { state: current, ownerToken: owned.ownerToken },
      signal,
    );
  } finally {
    if (owned) await releaseV2Owner(owned);
  }
}

async function bootstrapAndPublishNonce(
  storage: Storage,
  nonce: string,
  signal?: AbortSignal,
): Promise<void> {
  try {
    // V1 retains the original Web Locks behavior: a started request completes
    // before publishing its nonce, and is not aborted mid-commit.
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

async function ensureV1BrowserAuthContext(
  locks: BrowserLockManager,
  storage: Storage,
  signal?: AbortSignal,
): Promise<void> {
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

/**
 * Establish the browser auth context through the unchanged Web Locks V1 path,
 * or a fail-closed IDB lease and V2 generation fence when Web Locks is absent.
 */
export async function ensureBrowserAuthContext(
  signal?: AbortSignal,
  options: { forceBootstrap?: boolean } = {},
): Promise<void> {
  throwIfAborted(signal);
  const storage = browserStorage();
  const locks = browserLocks();
  if (locks) {
    if (!storage) throw unavailable();
    await ensureV1BrowserAuthContext(locks, storage, signal);
    return;
  }
  try {
    await ensureV2BrowserAuthContext(signal, options.forceBootstrap === true);
  } catch (error) {
    if (error instanceof BrowserAuthCoordinatorError || isAbortError(error) || error instanceof ApiRequestError) {
      throw error;
    }
    throw unavailable();
  }
}
