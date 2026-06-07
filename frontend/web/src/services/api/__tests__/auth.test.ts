import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { authApi, buildOAuthLoginUrl } from "../auth.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { refreshTokens } from "../tokenManager.ts";

function installAuthApiBrowserStubs(
  responseBody: Record<string, unknown> = {
    access_token: "access-token",
    refresh_token: "refresh-token",
    token_type: "bearer",
  },
) {
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const stored = new Map<string, string>();
  const events: string[] = [];
  const fetchCalls: string[] = [];

  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async (input: RequestInfo | URL) => {
      fetchCalls.push(String(input));
      return new Response(JSON.stringify(responseBody), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => stored.get(key) ?? null,
      setItem: (key: string, value: string) => {
        stored.set(key, value);
      },
      removeItem: (key: string) => {
        stored.delete(key);
      },
    },
  });
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      dispatchEvent(event: Event) {
        events.push(event.type);
        return true;
      },
    },
  });

  return {
    events,
    fetchCalls,
    stored,
    restore() {
      if (originalFetch) {
        Object.defineProperty(globalThis, "fetch", originalFetch);
      } else {
        delete (globalThis as { fetch?: typeof fetch }).fetch;
      }
      if (originalLocalStorage) {
        Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
      } else {
        delete (globalThis as { localStorage?: Storage }).localStorage;
      }
      if (originalWindow) {
        Object.defineProperty(globalThis, "window", originalWindow);
      } else {
        delete (globalThis as { window?: Window }).window;
      }
    },
  };
}

test("buildOAuthLoginUrl keeps same-origin deployments relative", () => {
  assert.equal(buildOAuthLoginUrl("google"), "/api/auth/oauth/google");
});

test("login clears auth-scoped preview caches before replacing tokens", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await authApi.login({ username: "user@example.com", password: "secret" });

    assert.equal(clearCount, 1);
    assert.equal(stubs.stored.get("access_token"), "access-token");
    assert.equal(stubs.stored.get("refresh_token"), "refresh-token");
    assert.deepEqual(stubs.events, ["auth:login"]);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("OAuth token callback clears auth-scoped preview caches before replacing tokens", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await authApi.handleOAuthCallback("github", "code", "state");

    assert.equal(clearCount, 1);
    assert.equal(stubs.stored.get("access_token"), "access-token");
    assert.equal(stubs.stored.get("refresh_token"), "refresh-token");
  } finally {
    unregister();
    stubs.restore();
  }
});

test("silent refresh clears auth-scoped preview caches before replacing tokens", async () => {
  const stubs = installAuthApiBrowserStubs({
    access_token: "refreshed-access-token",
    refresh_token: "refreshed-refresh-token",
    token_type: "bearer",
  });
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    assert.notEqual(stubs.stored.get("access_token"), "refreshed-access-token");
    assert.notEqual(
      stubs.stored.get("refresh_token"),
      "refreshed-refresh-token",
    );
    clearCount += 1;
  });

  stubs.stored.set("access_token", "initial-access-token");
  stubs.stored.set("refresh_token", "initial-refresh-token");

  try {
    const refreshed = await refreshTokens();

    assert.equal(clearCount, 1);
    assert.equal(refreshed.access_token, "refreshed-access-token");
    assert.equal(stubs.stored.get("access_token"), "refreshed-access-token");
    assert.equal(stubs.stored.get("refresh_token"), "refreshed-refresh-token");
  } finally {
    unregister();
    stubs.restore();
  }
});

test("OAuth callback page clears auth-scoped caches before setting fragment tokens", () => {
  const callbackSource = readFileSync(
    new URL("../../../components/auth/OAuthCallback.tsx", import.meta.url),
    "utf8",
  );

  assert.match(callbackSource, /clearAuthScopedCaches/);
  assert.match(
    callbackSource,
    /clearAuthScopedCaches\(\)[\s\S]*setTokens\(accessToken,\s*refreshToken\)/,
  );
});
