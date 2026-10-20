import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  CompanyADLoginError,
  authApi,
  buildOAuthLoginUrl,
  mapPrincipalToUser,
} from "../auth.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { ApiRequestError } from "../fetch.ts";
import {
  refreshAccessToken,
  refreshTokens,
} from "../tokenManager.ts";
import { UploadRequestError, uploadApi } from "../upload.ts";

test("principal mapping keeps effective department scope and never derives it from a scalar", () => {
  const principal = {
    user_id: "member", display_name: "Member", tenant_id: "default", department_id: "110",
    roles: ["user"], permissions: [], is_admin: false, source: "company-login",
  };
  const legacy = mapPrincipalToUser(principal);
  assert.deepEqual(legacy.department_ids, []);
  assert.equal(legacy.department_authority_status, "unavailable");
  const current = mapPrincipalToUser({ ...principal, direct_department_ids: ["110", "210"],
    department_ids: ["100", "110", "200", "210"], department_authority_status: "available" });
  assert.deepEqual(current.direct_department_ids, ["110", "210"]);
  assert.deepEqual(current.department_ids, ["100", "110", "200", "210"]);
  assert.equal(current.department_authority_status, "available");
});

function installAuthApiBrowserStubs(
  responseBody:
    | Record<string, unknown>
    | ((callIndex: number) => unknown) = {
    user_id: "dev001",
    user_name: "dev001",
    display_name: "Developer",
    tenant_id: "default",
    department_id: "研发一部",
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
      const callIndex = fetchCalls.length;
      fetchCalls.push(String(input));
      fetchInit.push(init ?? {});
      const body =
        typeof responseBody === "function"
          ? responseBody(callIndex)
          : responseBody;
      return new Response(JSON.stringify(body), {
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

test("current-user projection preserves the authenticated tenant and department subject", async () => {
  const stubs = installAuthApiBrowserStubs();
  try {
    const user = await authApi.getCurrentUser();

    assert.equal(user.id, "dev001");
    assert.equal(user.tenant_id, "default");
    assert.equal(user.department_id, "研发一部");
  } finally {
    stubs.restore();
  }
});

test("AD login fetches GetADName before exchanging only its JWT", async () => {
  const token = "signed-company-jwt";
  const stubs = installAuthApiBrowserStubs((callIndex) =>
    callIndex === 0
      ? [
          {
            workid: "ad001",
            username: "ad001",
            cnname: "AD User",
            depart: "研发一部",
            role: "user",
            token,
          },
        ]
      : {
          user_id: "ad001",
          display_name: "AD User",
          tenant_id: "default",
          department_id: "研发一部",
          roles: ["user"],
          permissions: ["agent:use"],
          is_admin: false,
          source: "company-login",
        },
  );
  const controller = new AbortController();
  try {
    const companyJwt = await authApi.fetchCompanyADLogin(
      "http://company.test/api/login/GetADName",
      controller.signal,
    );
    await authApi.loginWithAD(companyJwt, controller.signal);

    assert.equal(companyJwt, token);
    assert.deepEqual(stubs.fetchCalls, [
      "http://company.test/api/login/GetADName",
      "/api/ai/auth/ad-login",
    ]);
    assert.equal(stubs.fetchInit[0].credentials, "include");
    assert.equal(stubs.fetchInit[0].cache, "no-store");
    const companySignal = stubs.fetchInit[0].signal as AbortSignal;
    const exchangeSignal = stubs.fetchInit[1].signal as AbortSignal;
    assert.equal(companySignal instanceof AbortSignal, true);
    assert.equal(exchangeSignal instanceof AbortSignal, true);
    assert.notEqual(companySignal, controller.signal);
    assert.notEqual(exchangeSignal, companySignal);
    controller.abort();
    assert.equal(companySignal.aborted, true);
    assert.equal(exchangeSignal.aborted, true);
    assert.deepEqual(JSON.parse(String(stubs.fetchInit[1].body)), { token });
  } finally {
    stubs.restore();
  }
});

test("GetADName failure has one explicit company login error", async () => {
  const stubs = installAuthApiBrowserStubs({}, 401);
  try {
    await assert.rejects(
      () => authApi.fetchCompanyADLogin("http://company.test/api/login/GetADName"),
      (error: unknown) => error instanceof CompanyADLoginError,
    );
    assert.deepEqual(stubs.fetchCalls, [
      "http://company.test/api/login/GetADName",
    ]);
  } finally {
    stubs.restore();
  }
});

test("AD configuration composes the caller cancellation signal", async () => {
  const stubs = installAuthApiBrowserStubs({ ad_login_url: null });
  const controller = new AbortController();
  try {
    await authApi.getADLoginConfig(controller.signal);

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/ad-login/config"]);
    const requestSignal = stubs.fetchInit[0].signal as AbortSignal;
    assert.equal(requestSignal instanceof AbortSignal, true);
    assert.notEqual(requestSignal, controller.signal);
    controller.abort();
    assert.equal(requestSignal.aborted, true);
  } finally {
    stubs.restore();
  }
});

test("current-user hydration keeps terminal refresh rejection caller-owned", async () => {
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

    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/me", "/api/ai/auth/refresh"]);
    assert.equal(
      stubs.stored.get("ai_platform_session_present"),
      "owned-session-marker",
    );
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
  }
});

test("subject-changing auth transports compose cancellation with a bounded timeout", async () => {
  const stubs = installAuthApiBrowserStubs();
  const controller = new AbortController();
  try {
    await authApi.bootstrapAuthContext(
      {
        nonce: "A".repeat(43),
        protocol_version: 2,
        browser_incarnation: "I".repeat(43),
        generation: 1,
      },
      controller.signal,
    );
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
    const requestSignals = stubs.fetchInit.map(
      (init) => init.signal as AbortSignal,
    );
    assert.equal(
      requestSignals.every(
        (signal) => signal instanceof AbortSignal && signal !== controller.signal,
      ),
      true,
    );
    controller.abort();
    assert.equal(requestSignals.every((signal) => signal.aborted), true);
  } finally {
    stubs.restore();
  }
});

test("V2 bootstrap transport preserves the discriminated incarnation and generation response", async () => {
  const stubs = installAuthApiBrowserStubs({
    status: "ready",
    protocol_version: 2,
    generation: 7,
  });
  try {
    const response = await authApi.bootstrapAuthContext({
      nonce: "N".repeat(43),
      protocol_version: 2,
      browser_incarnation: "I".repeat(43),
      generation: 7,
    });
    assert.deepEqual(response, {
      status: "ready",
      protocol_version: 2,
      generation: 7,
    });
    assert.deepEqual(JSON.parse(String(stubs.fetchInit[0].body)), {
      nonce: "N".repeat(43),
      protocol_version: 2,
      browser_incarnation: "I".repeat(43),
      generation: 7,
    });
  } finally {
    stubs.restore();
  }
});

test("V2 login-context recovery serializes only its explicit recovery discriminator", async () => {
  const stubs = installAuthApiBrowserStubs({
    status: "ready",
    protocol_version: 2,
    generation: 7,
  });
  try {
    await authApi.bootstrapAuthContext({
      nonce: "N".repeat(43),
      protocol_version: 2,
      browser_incarnation: "I".repeat(43),
      generation: 7,
      recovery_only: true,
    });
    assert.deepEqual(JSON.parse(String(stubs.fetchInit[0].body)), {
      nonce: "N".repeat(43),
      protocol_version: 2,
      browser_incarnation: "I".repeat(43),
      generation: 7,
      recovery_only: true,
    });
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

test("refresh functions use the Cookie grant without exposing a JWT or clearing identity", async () => {
  const stubs = installAuthApiBrowserStubs({
    principal: { user_id: "dev001", display_name: "Developer", tenant_id: "default",
      roles: ["user"], permissions: ["agent:use"], is_admin: false, source: "company-login" },
    authorization_changed: false,
  });
  let clearCount = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    clearCount += 1;
  });

  stubs.stored.set("ai_platform_session_present", "existing-marker");

  try {
    const refreshed = await refreshTokens();
    assert.equal(refreshed.access_token, "existing-marker");
    assert.equal(await refreshAccessToken(), "existing-marker");
    assert.equal("jwt" in refreshed, false);
    assert.ok(stubs.fetchInit.every((init) => init.credentials === "include" && init.body === "{}"));

    assert.equal(clearCount, 0);
    assert.equal(
      stubs.stored.get("ai_platform_session_present"),
      "existing-marker",
    );
    assert.deepEqual(stubs.events, ["auth:session-refreshed", "auth:session-refreshed"]);
    assert.deepEqual(stubs.fetchCalls, ["/api/ai/auth/refresh", "/api/ai/auth/refresh"]);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("multipart initiation without pre-execution rejection proof is never replayed", async () => {
  const stubs = installAuthApiBrowserStubs({ detail: "unauthorized" }, 401);
  stubs.stored.set("ai_platform_session_present", "existing-marker");
  try {
    const handle = uploadApi.uploadFile(
      new File(["fixture"], "fixture.txt", { type: "text/plain" }),
    );
    await assert.rejects(handle.promise, (error: unknown) =>
      error instanceof UploadRequestError && error.kind === "recoverable" && error.status === 401,
    );
    // An initiation rejection has no session to upload, complete, abort or replay.
    assert.deepEqual(stubs.fetchCalls, ["/api/ai/files/uploads", "/api/ai/auth/refresh"]);
    assert.equal(stubs.fetchInit[0].method, "POST");
    assert.equal(stubs.fetchInit[0].credentials, "include");
    assert.equal(new Headers(stubs.fetchInit[0].headers).has("Authorization"), false);
    assert.equal(stubs.stored.get("ai_platform_session_present"), "existing-marker");
    // The terminal refresh rejection signals recovery without replaying the upload.
    assert.deepEqual(stubs.events, ["auth:force-relogin"]);
  } finally {
    stubs.restore();
  }
});

test("file deletion uses the protected Files route", async () => {
  const stubs = installAuthApiBrowserStubs({
    file_id: "file-a",
    lifecycle_state: "delete_pending",
    deletion_state: "file_pending",
    reconcile_required: false,
  });

  try {
    await uploadApi.deleteFile("file/a");
    assert.deepEqual(stubs.fetchCalls, ["/api/ai/files/file%2Fa"]);
    assert.equal(stubs.fetchInit[0].method, "DELETE");
    assert.equal(stubs.fetchInit[0].credentials, "include");
  } finally {
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
    /await authApi\.handleOAuthCallback\([\s\S]*owner\.abortController\.signal/,
  );
  assert.doesNotMatch(useAuthSource, /completeOAuthSession/);
  assert.match(callbackSource, /searchParams\.get\("code"\)/);
  assert.match(callbackSource, /searchParams\.get\("state"\)/);
  assert.doesNotMatch(callbackSource, /access_token|refresh_token/);
});
