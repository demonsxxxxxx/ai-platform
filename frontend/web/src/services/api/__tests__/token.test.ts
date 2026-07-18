import test from "node:test";
import assert from "node:assert/strict";

import {
  getAccessToken,
  getRedirectPath,
  getRefreshToken,
  isSafeRedirectPath,
  migrateLegacyBearerStorage,
  parseAuthStorageEvent,
  setTokens,
} from "../token.ts";

function installSessionStorage() {
  const store = new Map<string, string>();
  const original = Object.getOwnPropertyDescriptor(
    globalThis,
    "sessionStorage",
  );

  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      removeItem: (key: string) => {
        store.delete(key);
      },
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    },
  });

  return () => {
    if (original) {
      Object.defineProperty(globalThis, "sessionStorage", original);
    } else {
      delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
    }
  };
}

function installLocalStorage(initial: Record<string, string> = {}) {
  const store = new Map<string, string>(Object.entries(initial));
  const removedKeys: string[] = [];
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");

  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      removeItem: (key: string) => {
        removedKeys.push(key);
        store.delete(key);
      },
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
    },
  });

  return {
    removedKeys,
    restore() {
      if (original) {
        Object.defineProperty(globalThis, "localStorage", original);
      } else {
        delete (globalThis as { localStorage?: Storage }).localStorage;
      }
    },
  };
}

test("auth routes are not valid post-login redirect targets", () => {
  assert.equal(isSafeRedirectPath("/auth/callback"), false);
  assert.equal(isSafeRedirectPath("/auth/login"), false);
  assert.equal(isSafeRedirectPath("/"), false);
  assert.equal(isSafeRedirectPath("/chat"), true);
  assert.equal(isSafeRedirectPath("/chat/session-1?panel=files"), true);
});

test("getRedirectPath discards stale OAuth callback redirects", () => {
  const restore = installSessionStorage();
  try {
    sessionStorage.setItem("redirect_after_login", "/auth/callback");

    assert.equal(getRedirectPath(), null);
    assert.equal(sessionStorage.getItem("redirect_after_login"), null);
  } finally {
    restore();
  }
});

test("token getters are pure reads and ignore legacy bearer values", () => {
  const restore = installLocalStorage({
    access_token: "legacy-access-token",
    refresh_token: "legacy-refresh-token",
  });

  try {
    assert.equal(getAccessToken(), null);
    assert.equal(getRefreshToken(), null);
    assert.deepEqual(restore.removedKeys, []);
    assert.equal(localStorage.getItem("access_token"), "legacy-access-token");
    assert.equal(localStorage.getItem("refresh_token"), "legacy-refresh-token");
  } finally {
    restore.restore();
  }
});

test("legacy bearer cleanup only runs through the explicit owner operation", () => {
  const storage = installLocalStorage({
    access_token: "legacy-access-token",
    refresh_token: "legacy-refresh-token",
  });

  try {
    migrateLegacyBearerStorage();

    assert.deepEqual(storage.removedKeys, ["access_token", "refresh_token"]);
  } finally {
    storage.restore();
  }
});

test("setTokens writes a fresh non-secret session marker and clears legacy bearer keys", () => {
  const storage = installLocalStorage({
    access_token: "legacy-access-token",
    refresh_token: "legacy-refresh-token",
  });

  try {
    setTokens("new-access-token", "new-refresh-token");

    assert.match(
      localStorage.getItem("ai_platform_session_present") ?? "",
      /^[a-f0-9]{64}$/i,
    );
    assert.deepEqual(storage.removedKeys, ["access_token", "refresh_token"]);
  } finally {
    storage.restore();
  }
});

test("cross-tab auth storage parsing treats session marker removal as logout", () => {
  assert.equal(
    parseAuthStorageEvent({
      key: "ai_platform_session_present",
      oldValue: "prior-marker",
      newValue: null,
    }),
    "logout",
  );
  assert.equal(
    parseAuthStorageEvent({
      key: "ai_platform_session_present",
      oldValue: null,
      newValue: "new-marker",
    }),
    "login",
  );
  assert.equal(
    parseAuthStorageEvent({
      key: "ai_platform_session_present",
      oldValue: "old-marker",
      newValue: "new-marker",
    }),
    "replacement",
  );
  assert.equal(
    parseAuthStorageEvent({
      key: "access_token",
      oldValue: "legacy",
      newValue: null,
    }),
    null,
  );
});
