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

test("missing Web Locks fails closed without opening IndexedDB or bootstrapping", async () => {
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
        throw new Error("IndexedDB must not be opened without Web Locks");
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
    assert.equal(openCalls, 0);
    assert.equal(bootstrapCalls, 0);
    assert.equal(stubs.values.has(BROWSER_AUTH_CONTEXT_NONCE_KEY), false);
  } finally {
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
