import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { authApi, buildOAuthLoginUrl } from "../auth.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { ApiRequestError } from "../fetch.ts";
import {
  CookieSessionRefreshUnsupportedError,
  refreshAccessToken,
  refreshTokens,
} from "../tokenManager.ts";
import { uploadApi } from "../upload.ts";

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

test("buildOAuthLoginUrl keeps same-origin deployments relative and preserves opaque state", () => {
  assert.equal(buildOAuthLoginUrl("google"), "/api/auth/oauth/google");
  assert.equal(
    buildOAuthLoginUrl("github", "opaque-state"),
    "/api/auth/oauth/github?state=opaque-state",
  );
});

test("current-user projection preserves the authenticated tenant subject", async () => {
  const stubs = installAuthApiBrowserStubs();
  try {
    const user = await authApi.getCurrentUser();

    assert.equal(user.id, "dev001");
    assert.equal(user.tenant_id, "default");
  } finally {
    stubs.restore();
  }
});

test("current-user hydration returns owned 401 without legacy refresh or logout side effects", async () => {
  const stubs = installAuthApiBrowserStubs({ detail: "unauthorized" }, 401);
  stubs.stored.set("ai_platform_session_present", "owned-session-marker");
  try {
    await assert.rejects(
      () => authApi.getCurrentUser(),
      (error: unknown) => {
        assert.equal(error instanceof ApiRequestError, true);
        assert.equal((error as ApiRequestError).status, 401);
        return true;
      },
    );

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/me"]);
    assert.equal(
      stubs.stored.get("ai_platform_session_present"),
      "owned-session-marker",
    );
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
  }
});

test("subject-changing auth transports forward their operation abort signal", async () => {
  const stubs = installAuthApiBrowserStubs();
  const controller = new AbortController();
  try {
    await authApi.bootstrapAuthContext("A".repeat(43), controller.signal);
    await authApi.getCurrentUser({ signal: controller.signal });
    await authApi.login(
      { username: "user@example.com", password: "safe-test" },
      undefined,
      controller.signal,
    );
    await authApi.beginOAuth("github", controller.signal);
    await authApi.handleOAuthCallback(
      "github",
      "code",
      "state",
      controller.signal,
    );
    await authApi.logout(controller.signal);

    assert.equal(stubs.fetchInit.length, 6);
    assert.equal(
      stubs.fetchInit.every((init) => init.signal === controller.signal),
      true,
    );
  } finally {
    stubs.restore();
  }
});

test("login transport leaves cache, marker, and identity events to the auth owner", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await authApi.login({ username: "user@example.com", password: "secret" });

    assert.equal(clearCount, 0);
    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/login"]);
    assert.equal(stubs.fetchInit[0].credentials, "include");
    assert.equal(stubs.stored.get("ai_platform_session_present"), undefined);
    assert.equal(stubs.stored.get("access_token"), undefined);
    assert.equal(stubs.stored.get("refresh_token"), undefined);
    assert.deepEqual(stubs.events, []);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("OAuth callback transport leaves local auth state to the auth owner", async () => {
  const stubs = installAuthApiBrowserStubs();
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  try {
    await authApi.handleOAuthCallback("github", "code", "state");

    assert.equal(clearCount, 0);
    assert.equal(stubs.stored.get("ai_platform_session_present"), undefined);
    assert.equal(stubs.stored.get("access_token"), undefined);
    assert.equal(stubs.stored.get("refresh_token"), undefined);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("legacy refresh functions fail closed without network or global auth side effects", async () => {
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
    for (const operation of [refreshTokens, refreshAccessToken]) {
      await assert.rejects(
        () => operation(),
        (error: unknown) =>
          error instanceof CookieSessionRefreshUnsupportedError,
      );
    }

    assert.equal(clearCount, 0);
    assert.equal(
      stubs.stored.get("ai_platform_session_present"),
      "existing-marker",
    );
    assert.deepEqual(stubs.events, []);
    assert.deepEqual(stubs.fetchCalls, []);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("legacy upload 401 cannot refresh or replay through the compatibility firewall", async () => {
  const stubs = installAuthApiBrowserStubs();
  const originalXhr = Object.getOwnPropertyDescriptor(
    globalThis,
    "XMLHttpRequest",
  );
  let sendCalls = 0;

  class UnauthorizedUploadRequest {
    status = 401;
    statusText = "Unauthorized";
    responseText = JSON.stringify({ detail: "unauthorized" });
    withCredentials = false;
    readonly upload = { addEventListener: () => undefined };
    private readonly listeners = new Map<
      string,
      Array<(event: Event) => void>
    >();

    addEventListener(type: string, listener: (event: Event) => void) {
      const listeners = this.listeners.get(type) ?? [];
      listeners.push(listener);
      this.listeners.set(type, listeners);
    }

    open() {}
    setRequestHeader() {}
    abort() {}

    send() {
      sendCalls += 1;
      queueMicrotask(() => {
        for (const listener of this.listeners.get("load") ?? []) {
          listener(new Event("load"));
        }
      });
    }
  }

  Object.defineProperty(globalThis, "XMLHttpRequest", {
    configurable: true,
    value: UnauthorizedUploadRequest as unknown as typeof XMLHttpRequest,
  });
  stubs.stored.set("ai_platform_session_present", "existing-marker");

  try {
    const handle = uploadApi.uploadFile(
      new File(["fixture"], "fixture.txt", { type: "text/plain" }),
    );
    await assert.rejects(handle.promise);

    assert.equal(sendCalls, 1);
    assert.deepEqual(stubs.fetchCalls, []);
    assert.equal(
      stubs.stored.get("ai_platform_session_present"),
      "existing-marker",
    );
    assert.deepEqual(stubs.events, []);
  } finally {
    if (originalXhr) {
      Object.defineProperty(globalThis, "XMLHttpRequest", originalXhr);
    } else {
      delete (globalThis as { XMLHttpRequest?: typeof XMLHttpRequest })
        .XMLHttpRequest;
    }
    stubs.restore();
  }
});

test("logout transport does not clear owner-managed browser auth state", async () => {
  const stubs = installAuthApiBrowserStubs({ status: "logged_out" });
  stubs.stored.set("ai_platform_session_present", "session-marker");
  stubs.stored.set("access_token", "legacy-access");
  stubs.stored.set("refresh_token", "legacy-refresh");

  try {
    await authApi.logout();

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/logout"]);
    assert.equal(stubs.fetchInit[0].method, "POST");
    assert.equal(stubs.fetchInit[0].credentials, "include");
    assert.equal(stubs.stored.get("ai_platform_session_present"), "session-marker");
    assert.equal(stubs.stored.get("access_token"), "legacy-access");
    assert.equal(stubs.stored.get("refresh_token"), "legacy-refresh");
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
  }
});

test("logout transport failure is typed and never exposes raw backend detail", async () => {
  const stubs = installAuthApiBrowserStubs(
    { detail: { message: "logout failed /private/token=secret" } },
    500,
  );
  stubs.stored.set("ai_platform_session_present", "session-marker");

  try {
    await assert.rejects(
      () => authApi.logout(),
      (error: unknown) => {
        assert.equal(error instanceof ApiRequestError, true);
        assert.equal((error as ApiRequestError).status, 500);
        assert.doesNotMatch((error as Error).message, /logout|private|token|secret/i);
        return true;
      },
    );

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/logout"]);
    assert.equal(stubs.stored.get("ai_platform_session_present"), "session-marker");
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
  }
});

test("OAuth callback delegates server session authority and never reads fragment tokens", () => {
  const useAuthSource = readFileSync(
    new URL("../../../hooks/useAuth.tsx", import.meta.url),
    "utf8",
  );
  const callbackSource = readFileSync(
    new URL("../../../components/auth/OAuthCallback.tsx", import.meta.url),
    "utf8",
  );

  assert.match(useAuthSource, /await ensureBrowserAuthContext\(owner\.abortController\.signal\)/);
  assert.match(
    useAuthSource,
    /await authApi\.handleOAuthCallback\([\s\S]*signal: owner\.abortController\.signal/,
  );
  assert.doesNotMatch(useAuthSource, /completeOAuthSession/);
  assert.match(callbackSource, /searchParams\.get\("code"\)/);
  assert.match(callbackSource, /searchParams\.get\("state"\)/);
  assert.doesNotMatch(callbackSource, /access_token|refresh_token/);
});
