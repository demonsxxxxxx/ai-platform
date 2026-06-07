import test from "node:test";
import assert from "node:assert/strict";

import { getRedirectPath, isSafeRedirectPath } from "../token.ts";

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
