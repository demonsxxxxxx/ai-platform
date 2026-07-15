import assert from "node:assert/strict";
import test from "node:test";

import {
  BROWSER_AUTH_CONTEXT_NONCE_KEY,
  BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY,
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

type FakeRequest<T> = {
  error: DOMException | null;
  result: T;
  onerror: ((event: Event) => void) | null;
  onsuccess: ((event: Event) => void) | null;
};

class FakeIndexedDbTransaction {
  error: DOMException | null = null;
  onabort: ((event: Event) => void) | null = null;
  oncomplete: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  private active = false;
  private aborted = false;
  private completed = false;
  private pending = 0;
  private readonly pendingOperations: Array<() => void> = [];
  private resolveFinished!: () => void;
  readonly finished = new Promise<void>((resolve) => {
    this.resolveFinished = resolve;
  });

  constructor(private readonly records: Map<string, unknown>) {}

  objectStore(_name: string) {
    return {
      get: (key: string) => this.request(() => this.records.get(key)),
      put: (value: unknown, key: string) =>
        this.request(() => {
          this.records.set(key, structuredClone(value));
          return key;
        }),
    };
  }

  start() {
    if (this.active || this.aborted) return;
    this.active = true;
    this.pendingOperations.splice(0).forEach((operation) => operation());
  }

  abort() {
    if (this.aborted || this.completed) return;
    this.aborted = true;
    this.error = new DOMException("Fake IndexedDB transaction aborted", "AbortError");
    queueMicrotask(() => {
      this.onabort?.(new Event("abort"));
      this.finish();
    });
  }

  private request<T>(operation: () => T): FakeRequest<T> {
    const request: FakeRequest<T> = {
      error: null,
      result: undefined as T,
      onerror: null,
      onsuccess: null,
    };
    const execute = () => {
      if (this.aborted) return;
      this.pending += 1;
      queueMicrotask(() => {
        if (this.aborted) {
          this.pending -= 1;
          return;
        }
        try {
          request.result = operation();
          request.onsuccess?.(new Event("success"));
        } catch (error) {
          request.error = error instanceof DOMException
            ? error
            : new DOMException("Fake IndexedDB request failed", "UnknownError");
          request.onerror?.(new Event("error"));
          this.abort();
        } finally {
          this.pending -= 1;
          this.completeWhenIdle();
        }
      });
    };
    if (this.active) execute();
    else this.pendingOperations.push(execute);
    return request;
  }

  private completeWhenIdle() {
    if (!this.aborted && this.active && this.pending === 0) {
      queueMicrotask(() => {
        if (!this.aborted && this.pending === 0) {
          this.oncomplete?.(new Event("complete"));
          this.finish();
        }
      });
    }
  }

  private finish() {
    if (this.completed) return;
    this.completed = true;
    this.resolveFinished();
  }
}

class FakeIndexedDb {
  private hasStore = false;
  private writeTail = Promise.resolve();
  readonly records = new Map<string, unknown>();

  readonly factory = {
    open: (_name: string, _version?: number) => {
      const request = {
        error: null as DOMException | null,
        result: undefined as unknown as IDBDatabase,
        onblocked: null as ((event: Event) => void) | null,
        onerror: null as ((event: Event) => void) | null,
        onupgradeneeded: null as ((event: IDBVersionChangeEvent) => void) | null,
        onsuccess: null as ((event: Event) => void) | null,
      };
      const database = {
        objectStoreNames: {
          contains: (_store: string) => this.hasStore,
        },
        createObjectStore: (_store: string) => {
          this.hasStore = true;
          return {};
        },
        transaction: (_store: string, mode: IDBTransactionMode) => {
          const transaction = new FakeIndexedDbTransaction(this.records);
          const begin = () => transaction.start();
          if (mode === "readwrite") {
            const previous = this.writeTail;
            this.writeTail = transaction.finished;
            void previous.then(begin);
          } else {
            queueMicrotask(begin);
          }
          return transaction as unknown as IDBTransaction;
        },
      } as unknown as IDBDatabase;
      queueMicrotask(() => {
        request.result = database;
        if (!this.hasStore) {
          request.onupgradeneeded?.(new Event("upgradeneeded") as IDBVersionChangeEvent);
        }
        request.onsuccess?.(new Event("success"));
      });
      return request as unknown as IDBOpenDBRequest;
    },
  } as unknown as IDBFactory;
}

function installFallbackCoordinatorStubs(indexedDb?: IDBFactory) {
  const stubs = installBrowserCoordinatorStubs();
  Object.defineProperty(globalThis, "navigator", {
    configurable: true,
    value: {},
  });
  if (indexedDb) {
    Object.defineProperty(globalThis, "indexedDB", {
      configurable: true,
      value: indexedDb,
    });
  } else {
    delete (globalThis as { indexedDB?: IDBFactory }).indexedDB;
  }
  return stubs;
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
    submitted.push(nonce);
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
  const stubs = installFallbackCoordinatorStubs();
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

test("IndexedDB fallback shares one pending nonce across concurrent and late callers", async () => {
  const indexedDb = new FakeIndexedDb();
  const stubs = installFallbackCoordinatorStubs(indexedDb.factory);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const submitted: string[] = [];
  const firstBootstrapStarted = deferred<void>();
  const releaseFirstBootstrap = deferred<void>();
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(nonce);
    if (submitted.length === 1) {
      firstBootstrapStarted.resolve();
      await releaseFirstBootstrap.promise;
    }
  };

  try {
    const older = ensureBrowserAuthContext();
    await firstBootstrapStarted.promise;
    const newer = ensureBrowserAuthContext();

    assert.deepEqual(submitted, [submitted[0]]);
    releaseFirstBootstrap.resolve();
    await Promise.all([older, newer]);
    await ensureBrowserAuthContext();

    assert.equal(submitted.length, 3);
    assert.equal(new Set(submitted).size, 1);
    assert.equal(stubs.values.has(BROWSER_AUTH_CONTEXT_NONCE_KEY), false);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("IndexedDB fallback preserves a fresh owner generation from a late expired owner", async () => {
  const indexedDb = new FakeIndexedDb();
  const stubs = installFallbackCoordinatorStubs(indexedDb.factory);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const originalNow = Date.now;
  const submitted: string[] = [];
  const firstBootstrapStarted = deferred<void>();
  const releaseFirstBootstrap = deferred<void>();
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(nonce);
    if (submitted.length === 1) {
      firstBootstrapStarted.resolve();
      await releaseFirstBootstrap.promise;
    }
  };

  try {
    const staleOwner = ensureBrowserAuthContext();
    await firstBootstrapStarted.promise;
    Date.now = () => originalNow() + 60_000;

    await ensureBrowserAuthContext();
    releaseFirstBootstrap.resolve();
    await assert.rejects(
      () => staleOwner,
      (error: unknown) =>
        error instanceof BrowserAuthCoordinatorError &&
        error.code === "auth_context_coordination_unavailable",
    );
    await ensureBrowserAuthContext();

    assert.equal(submitted.length, 3);
    assert.equal(new Set(submitted).size, 1);
  } finally {
    Date.now = originalNow;
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("IndexedDB fallback rotates once under current ownership and preserves the rotated nonce", async () => {
  const indexedDb = new FakeIndexedDb();
  const stubs = installFallbackCoordinatorStubs(indexedDb.factory);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const submitted: string[] = [];
  authApi.bootstrapAuthContext = async (nonce) => {
    submitted.push(nonce);
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
    await ensureBrowserAuthContext();

    assert.equal(submitted.length, 3);
    assert.notEqual(submitted[0], submitted[1]);
    assert.equal(submitted[1], submitted[2]);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("IndexedDB fallback honors cancellation before acquisition without bootstrapping", async () => {
  const indexedDb = new FakeIndexedDb();
  const stubs = installFallbackCoordinatorStubs(indexedDb.factory);
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
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("IndexedDB fallback completes a started request despite later cancellation", async () => {
  const indexedDb = new FakeIndexedDb();
  const stubs = installFallbackCoordinatorStubs(indexedDb.factory);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const started = deferred<void>();
  const release = deferred<void>();
  const submitted: string[] = [];
  authApi.bootstrapAuthContext = async (nonce, signal) => {
    submitted.push(nonce);
    assert.equal(signal, undefined);
    started.resolve();
    await release.promise;
  };
  const controller = new AbortController();

  try {
    const operation = ensureBrowserAuthContext(controller.signal);
    await started.promise;
    controller.abort();
    release.resolve();
    await operation;
    await ensureBrowserAuthContext();

    assert.equal(submitted.length, 2);
    assert.equal(submitted[0], submitted[1]);
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("IndexedDB corruption fails closed before bootstrap", async () => {
  const indexedDb = new FakeIndexedDb();
  indexedDb.records.set(BROWSER_AUTH_CONTEXT_IDB_RECORD_KEY, { corrupt: true });
  const stubs = installFallbackCoordinatorStubs(indexedDb.factory);
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
  } finally {
    authApi.bootstrapAuthContext = originalBootstrap;
    stubs.restore();
  }
});

test("IndexedDB open timeout fails closed before bootstrap", async () => {
  const hangingIndexedDb = {
    open: () => ({}),
  } as unknown as IDBFactory;
  const stubs = installFallbackCoordinatorStubs(hangingIndexedDb);
  const originalBootstrap = authApi.bootstrapAuthContext;
  const originalSetTimeout = globalThis.setTimeout;
  let bootstrapCalls = 0;
  authApi.bootstrapAuthContext = async () => {
    bootstrapCalls += 1;
  };
  Object.defineProperty(globalThis, "setTimeout", {
    configurable: true,
    value: (callback: TimerHandler) => {
      queueMicrotask(() => {
        if (typeof callback === "function") callback();
      });
      return 0;
    },
  });

  try {
    await assert.rejects(
      () => ensureBrowserAuthContext(),
      (error: unknown) =>
        error instanceof BrowserAuthCoordinatorError &&
        error.code === "auth_context_coordination_unavailable",
    );
    assert.equal(bootstrapCalls, 0);
  } finally {
    Object.defineProperty(globalThis, "setTimeout", {
      configurable: true,
      value: originalSetTimeout,
    });
    authApi.bootstrapAuthContext = originalBootstrap;
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
    submitted.push(nonce);
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
    submitted.push(nonce);
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
    submitted.push(nonce);
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
    submitted.push(nonce);
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
