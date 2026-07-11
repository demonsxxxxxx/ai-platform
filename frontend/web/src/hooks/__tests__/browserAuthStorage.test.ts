import assert from "node:assert/strict";
import test from "node:test";

import { registerAuthScopedCacheClearer } from "../../services/api/authCacheInvalidation.ts";
import { AUTH_SESSION_MARKER_KEY } from "../../services/api/token.ts";
import { handleBrowserAuthStorageEvent } from "../browserAuthStorage.ts";

function installBrowserLogoutStubs() {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const events: string[] = [];
  const removedKeys: string[] = [];
  let fetchCalls = 0;

  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      dispatchEvent(event: Event) {
        events.push(event.type);
        return true;
      },
    },
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      removeItem(key: string) {
        removedKeys.push(key);
      },
    },
  });
  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: () => {
      fetchCalls += 1;
      return Promise.resolve(new Response(null, { status: 204 }));
    },
  });

  return {
    events,
    removedKeys,
    get fetchCalls() {
      return fetchCalls;
    },
    restore() {
      if (originalWindow) {
        Object.defineProperty(globalThis, "window", originalWindow);
      } else {
        delete (globalThis as { window?: Window }).window;
      }
      if (originalLocalStorage) {
        Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
      } else {
        delete (globalThis as { localStorage?: Storage }).localStorage;
      }
      if (originalFetch) {
        Object.defineProperty(globalThis, "fetch", originalFetch);
      } else {
        delete (globalThis as { fetch?: typeof fetch }).fetch;
      }
    },
  };
}

test("storage-event logout clears auth-scoped caches without backend logout or refresh", () => {
  const stubs = installBrowserLogoutStubs();
  let cacheClearCount = 0;
  let refreshCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    cacheClearCount += 1;
  });

  try {
    handleBrowserAuthStorageEvent(
      {
        key: AUTH_SESSION_MARKER_KEY,
        oldValue: "marker-before",
        newValue: null,
      } as StorageEvent,
      () => {
        refreshCount += 1;
      },
    );

    assert.equal(cacheClearCount, 1);
    assert.equal(refreshCount, 0);
    assert.equal(stubs.fetchCalls, 0);
    assert.deepEqual(stubs.events, ["auth:logout"]);
    assert.deepEqual(stubs.removedKeys, [
      "ai_platform_session_present",
      "access_token",
      "refresh_token",
    ]);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("storage-event login refreshes local user view without marker rewrite", () => {
  const stubs = installBrowserLogoutStubs();
  let refreshCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    throw new Error("login storage event must not clear auth-scoped caches");
  });

  try {
    handleBrowserAuthStorageEvent(
      {
        key: AUTH_SESSION_MARKER_KEY,
        oldValue: "marker-before",
        newValue: "marker-after",
      } as StorageEvent,
      () => {
        refreshCount += 1;
      },
    );

    assert.equal(refreshCount, 1);
    assert.equal(stubs.fetchCalls, 0);
    assert.deepEqual(stubs.events, []);
    assert.deepEqual(stubs.removedKeys, []);
  } finally {
    unregister();
    stubs.restore();
  }
});
