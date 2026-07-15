import assert from "node:assert/strict";
import test from "node:test";

import {
  BROWSER_AUTH_CONTEXT_NONCE_KEY,
  BrowserAuthCoordinatorError,
  ensureBrowserAuthContext,
} from "../browserAuthCoordinator.ts";
import { authApi } from "../../services/api/auth.ts";
import { ApiRequestError } from "../../services/api/fetch.ts";

function storageStub(values = new Map<string, string>()): Storage {
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
    removeItem: (key) => {
      values.delete(key);
    },
    clear: () => values.clear(),
    key: (index) => [...values.keys()][index] ?? null,
    get length() {
      return values.size;
    },
  };
}

function serialLocks() {
  let tail = Promise.resolve();
  return {
    request<T>(
      _name: string,
      _options: { mode: "exclusive" },
      callback: () => Promise<T>,
    ): Promise<T> {
      const current = tail.then(callback);
      tail = current.then(
        () => undefined,
        () => undefined,
      );
      return current;
    },
  };
}

function deferred<T = void>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function legacyNonce(request: unknown): string {
  assert.equal(typeof request, "string", "Web Locks path must keep the V1 string request interface");
  return request as string;
}

/**
 * Deterministic IDB event model, not a Map shim. It drives open upgrade/
 * blocked/versionchange events and queued readwrite transaction completion,
 * abort, rollback, and late completion explicitly.
 */
function installTransactionalIndexedDb(options: { holdGets?: boolean; blockNextOpen?: boolean } = {}) {
  const originalIndexedDb = Object.getOwnPropertyDescriptor(globalThis, "indexedDB");
  const records = new Map<string, unknown>();
  const heldGets: Array<() => void> = [];
  const transactions = new Set<FakeTransaction>();
  const requests: Array<Record<string, unknown>> = [];
  const databases = new Set<FakeDatabase>();
  const getStarted = deferred<void>();
  let schemaCreated = false;
  let abortedTransactions = 0;
  let closedDatabases = 0;

  const clone = <T>(value: T): T => (
    value === undefined ? value : JSON.parse(JSON.stringify(value)) as T
  );

  class FakeRequest<T = unknown> {
    result!: T;
    error: DOMException | null = null;
    onsuccess: ((event: Event) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
  }

  class FakeTransaction {
    onabort: ((event: Event) => void) | null = null;
    onerror: ((event: Event) => void) | null = null;
    oncomplete: ((event: Event) => void) | null = null;
    private pending = 0;
    private completed = false;
    aborted = false;

    constructor(readonly database: FakeDatabase) {
      transactions.add(this);
    }

    objectStore() {
      return {
        get: (key: string) => {
          const request = new FakeRequest<unknown>();
          this.pending += 1;
          const deliver = () => {
            if (this.aborted) return;
            request.result = records.has(key) ? clone(records.get(key)) : undefined;
            request.onsuccess?.(new Event("success"));
            this.pending -= 1;
            this.finishIfIdle();
          };
          getStarted.resolve();
          if (options.holdGets) heldGets.push(deliver);
          else queueMicrotask(deliver);
          return request as unknown as IDBRequest<unknown>;
        },
        put: (value: { id: string }) => {
          const request = new FakeRequest<IDBValidKey>();
          this.pending += 1;
          queueMicrotask(() => {
            if (this.aborted) return;
            records.set(value.id, clone(value));
            request.result = value.id;
            request.onsuccess?.(new Event("success"));
            this.pending -= 1;
            this.finishIfIdle();
          });
          return request as unknown as IDBRequest<IDBValidKey>;
        },
      } as unknown as IDBObjectStore;
    }

    abort() {
      if (this.aborted || this.completed) return;
      this.aborted = true;
      abortedTransactions += 1;
      queueMicrotask(() => this.onabort?.(new Event("abort")));
    }

    private finishIfIdle() {
      if (this.aborted || this.completed || this.pending !== 0) return;
      queueMicrotask(() => {
        if (this.aborted || this.completed || this.pending !== 0) return;
        this.completed = true;
        transactions.delete(this);
        this.oncomplete?.(new Event("complete"));
      });
    }
  }

  class FakeDatabase {
    closed = false;
    onversionchange: ((event: Event) => void) | null = null;
    objectStoreNames = {
      contains: () => schemaCreated,
    } as unknown as DOMStringList;

    createObjectStore() {
      schemaCreated = true;
      return {} as IDBObjectStore;
    }

    transaction() {
      if (this.closed) throw new DOMException("database closed", "InvalidStateError");
      return new FakeTransaction(this) as unknown as IDBTransaction;
    }

    close() {
      if (this.closed) return;
      this.closed = true;
      closedDatabases += 1;
    }
  }

  const factory = {
    open: () => {
      const request = {
        result: new FakeDatabase(),
        error: null,
        transaction: {
          aborted: false,
          abort() {
            this.aborted = true;
          },
        },
        onupgradeneeded: null as ((event: Event) => void) | null,
        onblocked: null as ((event: Event) => void) | null,
        onerror: null as ((event: Event) => void) | null,
        onsuccess: null as ((event: Event) => void) | null,
      };
      databases.add(request.result);
      requests.push(request as unknown as Record<string, unknown>);
      queueMicrotask(() => {
        if (options.blockNextOpen) {
          options.blockNextOpen = false;
          request.onblocked?.(new Event("blocked"));
          queueMicrotask(() => request.onsuccess?.(new Event("success")));
          return;
        }
        if (!schemaCreated) request.onupgradeneeded?.(new Event("upgradeneeded"));
        if (!request.transaction.aborted) request.onsuccess?.(new Event("success"));
      });
      return request as unknown as IDBOpenDBRequest;
    },
  } as unknown as IDBFactory;
  Object.defineProperty(globalThis, "indexedDB", { configurable: true, value: factory });

  return {
    records,
    requests,
    getStarted: getStarted.promise,
    get abortedTransactions() {
      return abortedTransactions;
    },
    get closedDatabases() {
      return closedDatabases;
    },
    get versionchangeHandlerCount() {
      return [...databases].filter((database) => database.onversionchange !== null).length;
    },
    currentState() {
      return clone(records.get("current"));
    },
    expireCurrentLease() {
      const state = records.get("current") as { leaseExpiresAt?: number } | undefined;
      if (state) state.leaseExpiresAt = 0;
    },
    releaseHeldGets() {
      const queued = heldGets.splice(0);
      for (const deliver of queued) deliver();
    },
    triggerVersionchange() {
      for (const database of databases) database.onversionchange?.(new Event("versionchange"));
      for (const transaction of transactions) transaction.abort();
    },
    restore() {
      if (originalIndexedDb) Object.defineProperty(globalThis, "indexedDB", originalIndexedDb);
      else delete (globalThis as { indexedDB?: IDBFactory }).indexedDB;
    },
  };
}

function installBrowserCoordinatorStubs() {
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const originalNavigator = Object.getOwnPropertyDescriptor(
    globalThis,
    "navigator",
  );
  const originalIndexedDb = Object.getOwnPropertyDescriptor(
    globalThis,
    "indexedDB",
  );
  const values = new Map<string, string>();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: storageStub(values),
  });
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: { locks: serialLocks() },
  });

  return {
    values,
    restore() {
      if (originalLocalStorage) {
        Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
      } else {
        delete (globalThis as { localStorage?: Storage }).localStorage;
      }
      if (originalNavigator) {
        Object.defineProperty(globalThis, "navigator", originalNavigator);
      } else {
        delete (globalThis as { navigator?: Navigator }).navigator;
      }
      if (originalIndexedDb) {
        Object.defineProperty(globalThis, "indexedDB", originalIndexedDb);
      } else {
        delete (globalThis as { indexedDB?: IDBFactory }).indexedDB;
      }
    },
  };
}

test("concurrent and late bootstrap operations use one stable browser nonce", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const originalBootstrap = authApi.bootstrapAuthContext;
  const submitted: string[] = [];
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(legacyNonce(nonce));
  };

  try {
    await Promise.all([
      ensureBrowserAuthContext(),
      ensureBrowserAuthContext(),
    ]);
    await ensureBrowserAuthContext();

    assert.equal(submitted.length, 3);
    assert.equal(new Set(submitted).size, 1);
    assert.equal(
      stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY),
      submitted[0],
    );
    assert.match(submitted[0], /^[A-Za-z0-9_-]{43,512}$/);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("missing safe browser coordinators fails closed before generating a context nonce", async () => {
  const stubs = installBrowserCoordinatorStubs();
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {},
  });
  delete (globalThis as { indexedDB?: IDBFactory }).indexedDB;
  const originalBootstrap = authApi.bootstrapAuthContext;
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
  };

  try {
    await assert.rejects(
      () => ensureBrowserAuthContext(),
      (error: unknown) =>
        error instanceof BrowserAuthCoordinatorError &&
        error.code === "auth_context_coordination_unavailable",
    );
    assert.equal(bootstrapCalls, 0);
    assert.equal(stubs.values.has(BROWSER_AUTH_CONTEXT_NONCE_KEY), false);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("missing Web Locks uses IndexedDB and fails closed before bootstrap when opening it fails", async () => {
  const stubs = installBrowserCoordinatorStubs();
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {},
  });
  let openCalls = 0;
  Object.defineProperty(globalThis, "indexedDB", {
    configurable: true,
    value: {
      open: () => {
        openCalls += 1;
        throw new Error("IndexedDB is unavailable");
      },
    } as unknown as IDBFactory,
  });
  const originalBootstrap = authApi.bootstrapAuthContext;
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
  };

  try {
    await assert.rejects(
      () => ensureBrowserAuthContext(),
      (error: unknown) =>
        error instanceof BrowserAuthCoordinatorError &&
        error.code === "auth_context_coordination_unavailable",
    );
    assert.equal(openCalls, 1);
    assert.equal(bootstrapCalls, 0);
    assert.equal(stubs.values.has(BROWSER_AUTH_CONTEXT_NONCE_KEY), false);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("two no-cookie tabs share one transactional V2 identity and bootstrap once", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb();
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  const firstStarted = deferred<void>();
  const releaseFirst = deferred<void>();
  const submitted: Array<Record<string, unknown>> = [];
  authApi.bootstrapAuthContext = async (request) => {
    assert.notEqual(typeof request, "string");
    const v2 = request as Record<string, unknown>;
    submitted.push(v2);
    if (submitted.length === 1) {
      firstStarted.resolve();
      await releaseFirst.promise;
    }
    return {
      status: "ready",
      protocol_version: 2,
      generation: v2.generation as number,
    };
  };

  try {
    const first = ensureBrowserAuthContext();
    await firstStarted.promise;
    const second = ensureBrowserAuthContext();
    releaseFirst.resolve();
    await Promise.all([first, second]);

    assert.equal(submitted.length, 1);
    assert.equal(submitted[0].protocol_version, 2);
    assert.match(submitted[0].browser_incarnation as string, /^[A-Za-z0-9_-]{43}$/);
    assert.equal(submitted[0].generation, 1);
    assert.match(submitted[0].nonce as string, /^[A-Za-z0-9_-]{43,512}$/);
    const state = idb.currentState() as { incarnation: string; currentGeneration: number; currentNonce: string; confirmedGeneration: number };
    assert.equal(state.incarnation, submitted[0].browser_incarnation);
    assert.equal(state.currentGeneration, 1);
    assert.equal(state.confirmedGeneration, 1);
    assert.equal(state.currentNonce, submitted[0].nonce);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("a forced V2 stale-cookie repair bypasses confirmed state exactly once", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb();
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  const submitted: Array<Record<string, unknown>> = [];
  authApi.bootstrapAuthContext = async (request) => {
    assert.notEqual(typeof request, "string");
    const v2 = request as Record<string, unknown>;
    submitted.push(v2);
    return {
      status: "ready",
      protocol_version: 2,
      generation: v2.generation as number,
    };
  };

  try {
    await ensureBrowserAuthContext();
    await ensureBrowserAuthContext();
    await ensureBrowserAuthContext(undefined, { forceBootstrap: true });

    assert.equal(submitted.length, 2);
    assert.deepEqual(
      submitted.map((request) => request.generation),
      [1, 1],
    );
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("blocked IDB open fails closed, closes late success, and cleans open handlers", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb({ blockNextOpen: true });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
    return { status: "ready", protocol_version: 2, generation: 1 };
  };

  try {
    await assert.rejects(
      () => ensureBrowserAuthContext(),
      (error: unknown) => error instanceof BrowserAuthCoordinatorError,
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(bootstrapCalls, 0);
    assert.equal(idb.closedDatabases, 1);
    const request = idb.requests[0] as {
      onupgradeneeded: unknown;
      onblocked: unknown;
      onerror: unknown;
      onsuccess: unknown;
    };
    assert.equal(request.onupgradeneeded, null);
    assert.equal(request.onblocked, null);
    assert.equal(request.onerror, null);
    assert.equal(request.onsuccess, null);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("queued V2 transaction aborts and rolls back before bootstrap", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb({ holdGets: true });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
    return { status: "ready", protocol_version: 2, generation: 1 };
  };
  const controller = new AbortController();

  try {
    const operation = ensureBrowserAuthContext(controller.signal);
    await idb.getStarted;
    controller.abort();
    idb.releaseHeldGets();
    await assert.rejects(
      () => operation,
      (error: unknown) => error instanceof DOMException && error.name === "AbortError",
    );
    assert.equal(idb.abortedTransactions, 1);
    assert.equal(idb.currentState(), undefined);
    assert.equal(bootstrapCalls, 0);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("versionchange aborts queued V2 work and releases its handler", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb({ holdGets: true });
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  authApi.bootstrapAuthContext = async () => {
    throw new Error("bootstrap must not start after versionchange");
  };

  try {
    const operation = ensureBrowserAuthContext();
    await idb.getStarted;
    idb.triggerVersionchange();
    idb.releaseHeldGets();
    await assert.rejects(
      () => operation,
      (error: unknown) => error instanceof BrowserAuthCoordinatorError,
    );
    assert.equal(idb.abortedTransactions, 1);
    assert.equal(idb.versionchangeHandlerCount, 0);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("lease expiry during an in-flight bootstrap prevents the stale owner from publishing or releasing", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb();
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  const firstStarted = deferred<void>();
  const secondStarted = deferred<void>();
  const releaseFirst = deferred<void>();
  let calls = 0;
  authApi.bootstrapAuthContext = async (request) => {
    const v2 = request as { generation: number };
    calls += 1;
    if (calls === 1) {
      firstStarted.resolve();
      await releaseFirst.promise;
    } else {
      secondStarted.resolve();
    }
    return { status: "ready", protocol_version: 2, generation: v2.generation };
  };

  try {
    const staleOwner = ensureBrowserAuthContext();
    await firstStarted.promise;
    idb.expireCurrentLease();
    const currentOwner = ensureBrowserAuthContext();
    await secondStarted.promise;
    await currentOwner;
    releaseFirst.resolve();
    await assert.rejects(
      () => staleOwner,
      (error: unknown) => error instanceof BrowserAuthCoordinatorError,
    );
    assert.equal(calls, 2);
    const state = idb.currentState() as {
      ownerToken: string;
      currentGeneration: number;
      confirmedGeneration: number;
    };
    assert.equal(state.ownerToken, "");
    assert.equal(state.currentGeneration, 1);
    assert.equal(state.confirmedGeneration, 1);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("pending single-use rotation survives cancellation and only advances generation after server success", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const idb = installTransactionalIndexedDb();
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {} });
  const originalBootstrap = authApi.bootstrapAuthContext;
  const rotationStarted = deferred<void>();
  const submitted: Array<Record<string, unknown>> = [];
  let call = 0;
  authApi.bootstrapAuthContext = async (request, signal) => {
    const v2 = request as Record<string, unknown>;
    submitted.push(v2);
    call += 1;
    if (call === 1) {
      return {
        status: "rebootstrap_required",
        protocol_version: 2,
        generation: 1,
        rotation_ticket: "T".repeat(43),
      };
    }
    if (call === 2) {
      rotationStarted.resolve();
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
      });
    }
    return {
      status: "ready",
      protocol_version: 2,
      generation: v2.generation as number,
    };
  };
  const controller = new AbortController();

  try {
    const interrupted = ensureBrowserAuthContext(controller.signal);
    await rotationStarted.promise;
    controller.abort();
    await assert.rejects(() => interrupted, (error: unknown) => error instanceof DOMException);
    const pending = idb.currentState() as {
      currentGeneration: number;
      pendingRotation?: { baseGeneration: number; nextNonce: string; ticket: string };
      ownerToken: string;
    };
    assert.equal(pending.currentGeneration, 1);
    assert.equal(pending.ownerToken, "");
    assert.deepEqual(pending.pendingRotation?.baseGeneration, 1);
    assert.equal(pending.pendingRotation?.ticket, "T".repeat(43));

    await ensureBrowserAuthContext();
    const completed = idb.currentState() as {
      currentGeneration: number;
      confirmedGeneration: number;
      pendingRotation?: unknown;
    };
    assert.equal(submitted.length, 3);
    assert.equal(submitted[2].rotation_ticket, "T".repeat(43));
    assert.equal(completed.currentGeneration, 2);
    assert.equal(completed.confirmedGeneration, 2);
    assert.equal(completed.pendingRotation, undefined);

    await ensureBrowserAuthContext();
    assert.equal(submitted.length, 3);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    idb.restore();
    stubs.restore();
  }
});

test("rebootstrap-required rotates the nonce once under the origin lock", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const oldNonce = "A".repeat(43);
  stubs.values.set(BROWSER_AUTH_CONTEXT_NONCE_KEY, oldNonce);
  let lockCalls = 0;
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {
      locks: {
        async request<T>(
          _name: string,
          _options: { mode: "exclusive" },
          callback: () => Promise<T>,
        ): Promise<T> {
          lockCalls += 1;
          return callback();
        },
      },
    },
  });
  const originalBootstrap = authApi.bootstrapAuthContext;
  const submitted: string[] = [];
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(legacyNonce(nonce));
    if (submitted.length === 1) {
      throw new ApiRequestError(
        "safe rebootstrap requirement",
        409,
        "auth_context_rebootstrap_required",
      );
    }
  };

  try {
    await ensureBrowserAuthContext();

    assert.equal(lockCalls, 1);
    assert.deepEqual(submitted.slice(0, 1), [oldNonce]);
    assert.equal(submitted.length, 2);
    assert.notEqual(submitted[1], oldNonce);
    assert.equal(
      stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY),
      submitted[1],
    );
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("transport and store errors do not rotate or retry the nonce", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const oldNonce = "B".repeat(43);
  stubs.values.set(BROWSER_AUTH_CONTEXT_NONCE_KEY, oldNonce);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const failure = new ApiRequestError("safe store failure", 503);
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
    throw failure;
  };

  try {
    await assert.rejects(
      () => ensureBrowserAuthContext(),
      (error: unknown) => error === failure,
    );
    assert.equal(bootstrapCalls, 1);
    assert.equal(
      stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY),
      oldNonce,
    );
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("an already-aborted caller never enters browser auth coordination", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const originalBootstrap = authApi.bootstrapAuthContext;
  const controller = new AbortController();
  controller.abort();
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
  };

  try {
    await assert.rejects(
      () => ensureBrowserAuthContext(controller.signal),
      (error: unknown) =>
        error instanceof DOMException && error.name === "AbortError",
    );
    assert.equal(bootstrapCalls, 0);
    assert.equal(stubs.values.has(BROWSER_AUTH_CONTEXT_NONCE_KEY), false);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("a queued bootstrap cannot observe an older caller's unpublished nonce", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const originalBootstrap = authApi.bootstrapAuthContext;
  const submitted: string[] = [];
  const firstBootstrapStarted = deferred<void>();
  const releaseFirstBootstrap = deferred<void>();
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(legacyNonce(nonce));
    if (submitted.length === 1) {
      firstBootstrapStarted.resolve();
      await releaseFirstBootstrap.promise;
    }
  };

  try {
    const older = ensureBrowserAuthContext();
    await firstBootstrapStarted.promise;
    const candidate = submitted[0];

    assert.equal(stubs.values.has(BROWSER_AUTH_CONTEXT_NONCE_KEY), false);

    const newer = ensureBrowserAuthContext();
    assert.deepEqual(submitted, [candidate]);

    releaseFirstBootstrap.resolve();
    await Promise.all([older, newer]);

    assert.deepEqual(submitted, [candidate, candidate]);
    assert.equal(stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY), candidate);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("an abort before rebootstrap rotation preserves the committed nonce", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const oldNonce = "C".repeat(43);
  stubs.values.set(BROWSER_AUTH_CONTEXT_NONCE_KEY, oldNonce);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const firstBootstrapStarted = deferred<void>();
  const rejectFirstBootstrap = deferred<void>();
  const submitted: string[] = [];
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(legacyNonce(nonce));
    firstBootstrapStarted.resolve();
    await rejectFirstBootstrap.promise;
  };
  const controller = new AbortController();

  try {
    const operation = ensureBrowserAuthContext(controller.signal);
    await firstBootstrapStarted.promise;
    controller.abort();
    rejectFirstBootstrap.reject(
      new ApiRequestError(
        "safe rebootstrap requirement",
        409,
        "auth_context_rebootstrap_required",
      ),
    );

    await assert.rejects(
      () => operation,
      (error: unknown) =>
        error instanceof DOMException && error.name === "AbortError",
    );
    assert.deepEqual(submitted, [oldNonce]);
    assert.equal(stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY), oldNonce);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("a started rotation bootstrap publishes its nonce despite later cancellation", async () => {
  const stubs = installBrowserCoordinatorStubs();
  const oldNonce = "D".repeat(43);
  stubs.values.set(BROWSER_AUTH_CONTEXT_NONCE_KEY, oldNonce);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const rotationStarted = deferred<void>();
  const releaseRotation = deferred<void>();
  const submitted: string[] = [];
  const signals: Array<AbortSignal | undefined> = [];
  authApi.bootstrapAuthContext = async (nonce, signal) => {
    submitted.push(legacyNonce(nonce));
    signals.push(signal);
    if (submitted.length === 1) {
      throw new ApiRequestError(
        "safe rebootstrap requirement",
        409,
        "auth_context_rebootstrap_required",
      );
    }
    rotationStarted.resolve();
    await releaseRotation.promise;
  };
  const controller = new AbortController();

  try {
    const operation = ensureBrowserAuthContext(controller.signal);
    await rotationStarted.promise;
    const rotatedNonce = submitted[1];

    assert.equal(stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY), oldNonce);
    controller.abort();
    releaseRotation.resolve();
    await operation;

    assert.deepEqual(signals, [undefined, undefined]);
    assert.equal(stubs.values.get(BROWSER_AUTH_CONTEXT_NONCE_KEY), rotatedNonce);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});
