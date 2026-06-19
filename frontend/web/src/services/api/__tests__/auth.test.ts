import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { authApi, buildOAuthLoginUrl } from "../auth.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { refreshTokens } from "../tokenManager.ts";

const PRINCIPAL_RESPONSE = {
  user_id: "u001",
  user_name: "u001",
  display_name: "User One",
  tenant_id: "default",
  roles: ["user"],
  permissions: ["agent:use", "artifact:download"],
  is_admin: false,
  source: "company-login",
};

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

  const fetchRequests: Array<{
    url: string;
    method: string;
    body?: string | null;
    credentials?: RequestCredentials;
  }> = [];

  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: async (input: RequestInfo | URL, init?: RequestInit) => {
      fetchCalls.push(String(input));
      fetchRequests.push({
        url: String(input),
        method: init?.method ?? "GET",
        body: typeof init?.body === "string" ? init.body : null,
        credentials: init?.credentials,
      });
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
    fetchRequests,
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

test("login accepts ai-platform principal response without storing bearer tokens", async () => {
  const stubs = installAuthApiBrowserStubs(PRINCIPAL_RESPONSE);
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    const response = await authApi.login({
      username: "u001",
      password: "secret",
    });

    assert.equal(response.user_id, "u001");
    assert.equal(response.display_name, "User One");
    assert.deepEqual(response.permissions, ["agent:use", "artifact:download"]);
    assert.equal(clearCount, 1);
    assert.equal(stubs.stored.has("access_token"), false);
    assert.equal(stubs.stored.has("refresh_token"), false);
    assert.deepEqual(stubs.events, ["auth:login"]);
    assert.deepEqual(stubs.fetchRequests[0], {
      url: "/api/ai/auth/login",
      method: "POST",
      body: JSON.stringify({ username: "u001", password: "secret" }),
      credentials: "include",
    });
  } finally {
    unregister();
    stubs.restore();
  }
});

test("getCurrentUser maps ai-platform principal to frontend user shape", async () => {
  const stubs = installAuthApiBrowserStubs(PRINCIPAL_RESPONSE);

  try {
    const user = await authApi.getCurrentUser();

    assert.equal(user.id, "u001");
    assert.equal(user.username, "u001");
    assert.equal(user.metadata?.display_name, "User One");
    assert.equal(user.metadata?.tenant_id, "default");
    assert.deepEqual(user.roles, ["user"]);
    assert.deepEqual(user.permissions, ["agent:use", "artifact:download"]);
    assert.equal(stubs.fetchRequests[0]?.credentials, "include");
  } finally {
    stubs.restore();
  }
});

test("logout calls backend logout and clears local auth state", async () => {
  const stubs = installAuthApiBrowserStubs({ status: "logged_out" });
  stubs.stored.set("access_token", "legacy-access-token");
  stubs.stored.set("refresh_token", "legacy-refresh-token");

  try {
    await authApi.logout();

    assert.deepEqual(stubs.fetchRequests[0], {
      url: "/api/ai/auth/logout",
      method: "POST",
      body: null,
      credentials: "include",
    });
    assert.equal(stubs.stored.has("access_token"), false);
    assert.equal(stubs.stored.has("refresh_token"), false);
    assert.deepEqual(stubs.events, ["auth:logout"]);
  } finally {
    stubs.restore();
  }
});

test("OAuth token callback is fail-closed in Phase 1", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await assert.rejects(
      () => authApi.handleOAuthCallback("github", "code", "state"),
      /OAuth login is scheduled for Phase 2/,
    );

    assert.equal(clearCount, 0);
    assert.equal(stubs.stored.has("access_token"), false);
    assert.equal(stubs.stored.has("refresh_token"), false);
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

test("OAuth callback page is fail-closed without storing fragment tokens", () => {
  const callbackSource = readFileSync(
    new URL("../../../components/auth/OAuthCallback.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(callbackSource, /clearAuthScopedCaches/);
  assert.doesNotMatch(callbackSource, /setTokens/);
  assert.doesNotMatch(callbackSource, /access_token/);
  assert.doesNotMatch(callbackSource, /refresh_token/);
  assert.match(callbackSource, /oauth_phase2_unavailable/);
});
