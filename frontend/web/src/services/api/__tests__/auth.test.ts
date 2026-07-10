import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { authApi, buildOAuthLoginUrl } from "../auth.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { refreshTokens } from "../tokenManager.ts";

function installAuthApiBrowserStubs(
  responseBody: Record<string, unknown> = {
    user_id: "dev001",
    user_name: "dev001",
    display_name: "Developer",
    tenant_id: "default",
    roles: ["developer"],
    permissions: ["agent:use"],
    is_admin: true,
    source: "company-login",
  },
  status = 200,
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
  const fetchInit: RequestInit[] = [];

  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async (input: RequestInfo | URL, init?: RequestInit) => {
      fetchCalls.push(String(input));
      fetchInit.push(init ?? {});
      return new Response(JSON.stringify(responseBody), {
        status,
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
    fetchInit,
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

test("login clears auth-scoped preview caches and marks cookie session without storing bearer tokens", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await authApi.login({ username: "user@example.com", password: "secret" });

    assert.equal(clearCount, 1);
    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/login"]);
    assert.equal(stubs.fetchInit[0].credentials, "include");
    assert.match(
      stubs.stored.get("ai_platform_session_present") ?? "",
      /^\d+-[a-z0-9]+$/i,
    );
    assert.equal(stubs.stored.get("access_token"), undefined);
    assert.equal(stubs.stored.get("refresh_token"), undefined);
    assert.deepEqual(stubs.events, ["auth:login"]);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("OAuth token callback clears auth-scoped preview caches before marking cookie session", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await authApi.handleOAuthCallback("github", "code", "state");

    assert.equal(clearCount, 1);
    assert.match(
      stubs.stored.get("ai_platform_session_present") ?? "",
      /^\d+-[a-z0-9]+$/i,
    );
    assert.equal(stubs.stored.get("access_token"), undefined);
    assert.equal(stubs.stored.get("refresh_token"), undefined);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("cookie-session probe clears auth-scoped preview caches without restoring bearer tokens", async () => {
  const stubs = installAuthApiBrowserStubs({
    user_id: "dev001",
    user_name: "dev001",
    display_name: "Developer",
    tenant_id: "default",
    roles: ["developer"],
    permissions: ["agent:use"],
    is_admin: true,
    source: "company-login",
  });
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  stubs.stored.set("ai_platform_session_present", "existing-marker");

  try {
    const refreshed = await refreshTokens();

    assert.equal(clearCount, 1);
    assert.equal(refreshed.access_token, "cookie-session");
    assert.match(
      stubs.stored.get("ai_platform_session_present") ?? "",
      /^\d+-[a-z0-9]+$/i,
    );
    assert.equal(stubs.stored.get("access_token"), undefined);
    assert.equal(stubs.stored.get("refresh_token"), undefined);
    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/me"]);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("logout calls backend logout before clearing browser auth state", async () => {
  const stubs = installAuthApiBrowserStubs({ status: "logged_out" });
  stubs.stored.set("ai_platform_session_present", "session-marker");
  stubs.stored.set("access_token", "legacy-access");
  stubs.stored.set("refresh_token", "legacy-refresh");

  try {
    await authApi.logout();

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/logout"]);
    assert.equal(stubs.fetchInit[0].method, "POST");
    assert.equal(stubs.fetchInit[0].credentials, "include");
    assert.equal(stubs.stored.get("ai_platform_session_present"), undefined);
    assert.equal(stubs.stored.get("access_token"), undefined);
    assert.equal(stubs.stored.get("refresh_token"), undefined);
    assert.deepEqual(stubs.events, ["auth:logout"]);
  } finally {
    stubs.restore();
  }
});

test("logout keeps browser auth state when backend logout fails", async () => {
  const stubs = installAuthApiBrowserStubs(
    { detail: "logout_failed" },
    500,
  );
  stubs.stored.set("ai_platform_session_present", "session-marker");

  try {
    await assert.rejects(() => authApi.logout(), /logout_failed/i);

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/logout"]);
    assert.equal(stubs.stored.get("ai_platform_session_present"), "session-marker");
    assert.deepEqual(stubs.events, []);
  } finally {
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
