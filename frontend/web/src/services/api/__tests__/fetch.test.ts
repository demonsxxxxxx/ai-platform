import test from "node:test";
import assert from "node:assert/strict";

import { ApiProtocolError, ApiRequestError, authFetch, FORCE_RELOGIN_EVENT } from "../fetch.ts";
import { apiRequestErrorFromResponse } from "../fetch.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { parseSessionRunInputs, sessionApi } from "../session.ts";

import { AUTH_SESSION_MARKER_KEY } from "../token.ts";
import { authenticatedRequest } from "../authenticatedRequest.ts";

function installFetchAuthStubs({
  fetchImpl,
  initialLocalStorage = {},
}: {
  fetchImpl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
  initialLocalStorage?: Record<string, string>;
}) {
  const originalFetch = Object.getOwnPropertyDescriptor(globalThis, "fetch");
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const originalSessionStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "sessionStorage",
  );
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

  const store = new Map<string, string>(Object.entries(initialLocalStorage));
  const sessionStore = new Map<string, string>();
  const removedKeys: string[] = [];
  const events: string[] = [];

  Object.defineProperty(globalThis, "fetch", {
    configurable: true,
    value: fetchImpl,
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        store.set(key, value);
      },
      removeItem: (key: string) => {
        removedKeys.push(key);
        store.delete(key);
      },
    },
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => sessionStore.get(key) ?? null,
      setItem: (key: string, value: string) => {
        sessionStore.set(key, value);
      },
      removeItem: (key: string) => {
        sessionStore.delete(key);
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
      location: {
        pathname: "/chat",
        search: "",
      },
    },
  });

  return {
    removedKeys,
    events,
    store,
    sessionStore,
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
      if (originalSessionStorage) {
        Object.defineProperty(globalThis, "sessionStorage", originalSessionStorage);
      } else {
        delete (globalThis as { sessionStorage?: Storage }).sessionStorage;
      }
      if (originalWindow) {
        Object.defineProperty(globalThis, "window", originalWindow);
      } else {
        delete (globalThis as { window?: Window }).window;
      }
    },
  };
}

test("retains only server-controlled submission recovery fields", async () => {
  const error = await apiRequestErrorFromResponse(
    new Response(
      JSON.stringify({
        detail: {
          code: "session_workspace_mismatch",
          submission_disposition: "rejected_before_persist",
          diagnostic_id: "diag_0123456789abcdef",
          private_diagnostic: "must not be projected",
        },
      }),
      { status: 409, headers: { "Content-Type": "application/json" } },
    ),
  );

  assert.equal(error.code, "session_workspace_mismatch");
  assert.equal(error.submissionDisposition, "rejected_before_persist");
  assert.equal(error.diagnosticId, "diag_0123456789abcdef");
});

test("rejects malformed diagnostic identifiers", async () => {
  const error = await apiRequestErrorFromResponse(
    new Response(
      JSON.stringify({
        detail: {
          code: "chat_submission_internal_error",
          diagnostic_id: "diag_private-token",
        },
      }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    ),
  );

  assert.equal(error.code, "chat_submission_internal_error");
  assert.equal(error.diagnosticId, undefined);
  assert.doesNotMatch(error.message, /private|token/i);
});

test("authFetch uses cookie credentials without mutating legacy auth storage", async () => {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "session-marker",
      access_token: "legacy-access-token",
      refresh_token: "legacy-refresh-token",
    },
    fetchImpl: async (input, init) => {
      calls.push({ input: String(input), init });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  try {
    const response = await authFetch<{ ok: boolean }>("/api/sessions");

    assert.deepEqual(response, { ok: true });
    assert.equal(calls.length, 1);
    assert.equal(calls[0].input, "/api/sessions");
    assert.equal(calls[0].init?.credentials, "include");
    assert.equal(
      new Headers(calls[0].init?.headers).has("Authorization"),
      false,
    );
    assert.deepEqual(stubs.removedKeys, []);
    assert.equal(stubs.store.get("access_token"), "legacy-access-token");
    assert.equal(stubs.store.get("refresh_token"), "legacy-refresh-token");
  } finally {
    stubs.restore();
  }
});

test("authFetch strips caller-supplied Authorization headers in browser mode", async () => {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "session-marker",
    },
    fetchImpl: async (input, init) => {
      calls.push({ input: String(input), init });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  try {
    await authFetch("/api/sessions", {
      headers: {
        Authorization: "Bearer leaked-token",
        "X-Test": "1",
      },
    });

    const headers = new Headers(calls[0].init?.headers);
    assert.equal(headers.has("Authorization"), false);
    assert.equal(headers.get("X-Test"), "1");
  } finally {
    stubs.restore();
  }
});

test("authFetch emits recovery for a current ordinary 401", async () => {
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "session-marker",
    },
    fetchImpl: async () =>
      new Response(JSON.stringify({ detail: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      }),
  });

  try {
    await assert.rejects(() => authFetch("/api/ai/auth/me"), ApiRequestError);
    assert.deepEqual(stubs.events, ["auth:force-relogin"]);
  } finally {
    stubs.restore();
  }
});

test("authFetch never replays a stale POST or mutates a replacement marker after 401", async () => {
  const calls: Array<{ url: string; body: BodyInit | null | undefined }> = [];
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "marker-a",
    },
    fetchImpl: async (input, init) => {
      calls.push({ url: String(input), body: init?.body });
      localStorage.setItem("ai_platform_session_present", "marker-b");
      return new Response(JSON.stringify({ detail: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    },
  });
  let cacheClears = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    cacheClears += 1;
  });

  try {
    await assert.rejects(
      () =>
        authFetch("/api/sessions", {
          method: "POST",
          body: JSON.stringify({ owner: "a", value: "must-not-replay" }),
        }),
      (error: unknown) => {
        assert.equal(error instanceof ApiRequestError, true);
        assert.equal((error as ApiRequestError).status, 401);
        return true;
      },
    );
    assert.deepEqual(calls, [
      {
        url: "/api/sessions",
        body: JSON.stringify({ owner: "a", value: "must-not-replay" }),
      },
    ]);
    assert.equal(stubs.store.get("ai_platform_session_present"), "marker-b");
    assert.deepEqual(stubs.events, []);
    assert.equal(cacheClears, 0);
    assert.deepEqual(stubs.removedKeys, []);
    assert.equal(stubs.sessionStore.has("redirect_after_login"), false);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("authFetch ignores a forced relogin from a request started before login", async () => {
  const stubs = installFetchAuthStubs({
    fetchImpl: async () => {
      localStorage.setItem("ai_platform_session_present", "marker-b");
      return new Response(JSON.stringify({ detail: "unauthorized" }), {
        status: 401,
        headers: {
          "Content-Type": "application/json",
          "X-Force-Relogin": "true",
        },
      });
    },
  });

  try {
    await assert.rejects(() => authFetch("/api/ai/auth/me"), ApiRequestError);
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
  }
});

test("authFetch treats force-relogin as a typed safe error and emits the recovery signal", async () => {
  const calls: string[] = [];
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "session-marker",
    },
    fetchImpl: async (input) => {
      calls.push(String(input));
      return new Response(
        JSON.stringify({ detail: { message: "proxy token=private" } }),
        {
        status: 200,
          headers: {
            "Content-Type": "application/json",
            "X-Force-Relogin": "true",
          },
        },
      );
    },
  });

  try {
    await assert.rejects(
      () => authFetch("/api/sessions"),
      (error: unknown) => {
        assert.equal(error instanceof ApiRequestError, true);
        assert.equal((error as ApiRequestError).status, 401);
        assert.doesNotMatch((error as Error).message, /proxy|token|private/i);
        return true;
      },
    );
    assert.deepEqual(calls, ["/api/sessions"]);
    assert.equal(
      stubs.store.get("ai_platform_session_present"),
      "session-marker",
    );
    assert.deepEqual(stubs.events, ["auth:force-relogin"]);
    assert.deepEqual(stubs.removedKeys, []);
    assert.equal(stubs.sessionStore.size, 0);
  } finally {
    stubs.restore();
  }
});

test("authFetch preserves AbortError without translating or wrapping it", async () => {
  const abort = new DOMException("aborted", "AbortError");
  const stubs = installFetchAuthStubs({
    fetchImpl: async () => {
      throw abort;
    },
  });

  try {
    await assert.rejects(
      () => authFetch("/api/sessions"),
      (error: unknown) => error === abort,
    );
  } finally {
    stubs.restore();
  }
});

test("authFetch never projects raw response diagnostics into ApiRequestError messages", async () => {
  const cases: Array<{ status: number; body: string; contentType?: string }> = [
    { status: 401, body: JSON.stringify({ detail: "/srv/private?token=secret" }) },
    { status: 403, body: JSON.stringify({ detail: { message: "Bearer private" } }) },
    { status: 429, body: JSON.stringify({ detail: { nested: { code: "invalid_credentials" } } }) },
    { status: 502, body: "<html>proxy diagnostics token=secret</html>", contentType: "text/html" },
  ];

  for (const item of cases) {
    const stubs = installFetchAuthStubs({
      fetchImpl: async () =>
        new Response(item.body, {
          status: item.status,
          statusText: "private upstream token=secret",
          headers: { "Content-Type": item.contentType ?? "application/json" },
        }),
    });
    try {
      await assert.rejects(
        () => authFetch("/api/sessions"),
        (error: unknown) => {
          assert.equal(error instanceof ApiRequestError, true);
          assert.equal((error as ApiRequestError).status, item.status);
          assert.doesNotMatch(
            (error as Error).message,
            /private|token|proxy|html|upstream|srv/i,
          );
          return true;
        },
      );
    } finally {
      stubs.restore();
    }
  }
});

test("authFetch rejects malformed successful JSON without retaining or logging private diagnostics", async () => {
  const originalWarn = console.warn;
  const warnings: unknown[][] = [];
  console.warn = (...args: unknown[]) => {
    warnings.push(args);
  };

  try {
    const cases = [
      { status: 200, body: "secret=backend-cookie", contentType: "application/json" },
      { status: 201, body: '{"private":"secret","ok":', contentType: "application/json" },
      { status: 200, body: "<html>upstream token=secret</html>", contentType: "text/html" },
      { status: 200, body: "", contentType: "application/json" },
      { status: 200, body: " \n\t", contentType: "application/json" },
    ];
    for (const item of cases) {
      let calls = 0;
      const stubs = installFetchAuthStubs({
        initialLocalStorage: { ai_platform_session_present: "session-marker" },
        fetchImpl: async () => {
          calls += 1;
          return new Response(item.body, {
            status: item.status,
            statusText: "private upstream token=secret",
            headers: {
              "Content-Type": item.contentType,
              "X-Request-ID": "/private?token=secret",
            },
          });
        },
      });
      try {
        await assert.rejects(
          () => authFetch("/api/sessions?token=secret", { method: "POST" }),
          (error: unknown) => {
            assert.ok(error instanceof ApiProtocolError);
            assert.ok(error instanceof ApiRequestError);
            assert.equal(error.name, "ApiProtocolError");
            assert.equal(error.status, item.status);
            assert.equal(error.code, "api_response_invalid");
            assert.equal(error.submissionDisposition, undefined);
            assert.equal(error.diagnosticId, undefined);
            assert.ok(error.message.length > 0);
            assert.doesNotMatch(
              `${error.message} ${JSON.stringify(error)}`,
              /private|token|secret|cookie|html|upstream|\/api\//i,
            );
            assert.equal(Object.prototype.hasOwnProperty.call(error, "cause"), false);
            return true;
          },
        );
        assert.equal(calls, 1);
        assert.deepEqual(stubs.events, []);
        assert.deepEqual(stubs.removedKeys, []);
        assert.equal(stubs.store.get("ai_platform_session_present"), "session-marker");
      } finally {
        stubs.restore();
      }
    }
    assert.deepEqual(warnings, []);
  } finally {
    console.warn = originalWarn;
  }
});

test("authFetch preserves the null result only for HTTP bodyless success contracts", async () => {
  for (const item of [
    { status: 204, method: "POST" },
    { status: 205, method: "POST" },
    { status: 200, method: "HEAD" },
    { status: 200, method: "head" },
  ]) {
    const response = new Response(null, { status: item.status });
    response.text = async () => {
      assert.fail("bodyless responses must not be consumed as JSON");
    };
    const stubs = installFetchAuthStubs({ fetchImpl: async () => response });
    try {
      assert.equal(await authFetch("/api/sessions", { method: item.method }), null);
    } finally {
      stubs.restore();
    }
  }
});

test("authFetch accepts valid JSON including explicit null without imposing a content-type migration", async () => {
  for (const value of [null, false, 0, "", [], { ok: true }]) {
    const stubs = installFetchAuthStubs({
      fetchImpl: async () => new Response(JSON.stringify(value)),
    });
    try {
      assert.deepEqual(await authFetch("/api/sessions"), value);
    } finally {
      stubs.restore();
    }
  }
});

test("authFetch preserves cancelled and failed body reads without wrapping or identity recovery", async () => {
  for (const status of [200, 401, 403, 500]) {
    for (const failure of [
      new DOMException("aborted", "AbortError"),
      new Error("custom abort reason"),
      new TypeError("connection terminated"),
    ]) {
      const stubs = installFetchAuthStubs({
        initialLocalStorage: { ai_platform_session_present: "session-marker" },
        fetchImpl: async () => new Response(new ReadableStream({
          start(controller) {
            controller.error(failure);
          },
        }), { status }),
      });
      try {
        await assert.rejects(
          () => authFetch("/api/sessions"),
          (error: unknown) => error === failure,
        );
        assert.deepEqual(stubs.events, []);
      } finally {
        stubs.restore();
      }
    }
  }
});

test("session Run input history binds its envelope and encodes the pagination cursor", async () => {
  const page = { session_id: "session/owned", runs: [{ run_id: "run-safe", state: "inactive", inputs: [], questions: [] }], has_more: true, next_before_run_id: "run-safe" };
  const requests: Array<{ url: string; init?: RequestInit }> = [];
  const env = installFetchAuthStubs({ fetchImpl: async (input, init) => {
    requests.push({ url: String(input), init });
    return new Response(JSON.stringify(page), { status: 200 });
  } });
  try {
    const result = await sessionApi.getRunInputHistory("session/owned", { beforeRunId: "run/older?", limit: 20 });
    assert.deepEqual(result, page);
    assert.equal(requests[0].url, "/api/ai/sessions/session%2Fowned/run-inputs?limit=20&before_run_id=run%2Folder%3F");
    assert.equal(requests[0].init?.cache, "no-store");
    assert.throws(() => parseSessionRunInputs({ ...page, session_id: "session-foreign" }, "session/owned"), /invalid_session_run_inputs_projection/);
    assert.throws(() => parseSessionRunInputs({ ...page, next_before_run_id: null }, "session/owned"), /invalid_session_run_inputs_projection/);
    assert.throws(() => parseSessionRunInputs({ ...page, runs: [page.runs[0], page.runs[0]] }, "session/owned"), /invalid_session_run_inputs_projection/);
    assert.throws(() => parseSessionRunInputs({ ...page, runs: [{ run_id: "run-safe", state: "bad", inputs: [], questions: [] }] }, "session/owned"), /invalid_run_inputs_projection/);
  } finally { env.restore(); }
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function environment(fetchImpl: typeof fetch) {
  const original = new Map(["fetch", "localStorage", "window"].map((key) => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  let marker = "owner-a";
  const events: string[] = [];
  Object.defineProperty(globalThis, "fetch", { configurable: true, value: fetchImpl });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: { getItem: (key: string) => key === AUTH_SESSION_MARKER_KEY ? marker : null } });
  Object.defineProperty(globalThis, "window", { configurable: true, value: { dispatchEvent: (event: Event) => { events.push(event.type); return true; } } });
  return {
    events,
    replaceOwner() { marker = "owner-b"; },
    restore() {
      for (const [key, descriptor] of original) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else Reflect.deleteProperty(globalThis, key);
      }
    },
  };
}

const errorBody = JSON.stringify({ detail: { message: "private credential=synthetic-secret" } });
function forceReloginResponse(): Response {
  return new Response(errorBody, { status: 401, headers: { "X-Force-Relogin": "true" } });
}
function assertSafeError(error: unknown) {
  assert.ok(error instanceof ApiRequestError);
  assert.equal(error.status, 401);
  assert.doesNotMatch(error.message, /private|credential|synthetic-secret/);
}

for (const [name, request] of [
  ["authFetch", authFetch], ["authenticatedRequest", authenticatedRequest],
] as const) {
  test(`${name} rejects stale force-relogin responses without invalidating the replacement owner`, async () => {
    const response = deferred<Response>();
    const started = deferred<void>();
    let calls = 0;
    const env = environment(async () => { calls += 1; started.resolve(); return response.promise; });
    try {
      const pending = request("/api/synthetic", { method: "POST", body: "synthetic" }).catch((error: unknown) => error);
      await started.promise;
      env.replaceOwner();
      response.resolve(forceReloginResponse());
      assertSafeError(await pending);
      assert.deepEqual(env.events, []);
      assert.equal(calls, 1);
    } finally { env.restore(); }
  });

  test(`${name} rechecks owner after a pending error-body read`, async () => {
    const body = deferred<string>();
    const reading = deferred<void>();
    let calls = 0;
    const env = environment(async () => {
      calls += 1;
      const response = forceReloginResponse();
      response.text = () => { reading.resolve(); return body.promise; };
      return response;
    });
    try {
      const pending = request("/api/synthetic").catch((error: unknown) => error);
      await reading.promise;
      env.replaceOwner();
      body.resolve(errorBody);
      assertSafeError(await pending);
      assert.deepEqual(env.events, []);
      assert.equal(calls, 1);
    } finally { env.restore(); }
  });

  test(`${name} still signals force-relogin for its current owner`, async () => {
    const env = environment(async () => forceReloginResponse());
    try {
      assertSafeError(await request("/api/synthetic").catch((error: unknown) => error));
      assert.deepEqual(env.events, [FORCE_RELOGIN_EVENT]);
    } finally { env.restore(); }
  });
}

test("authFetch rechecks owner after an ordinary 401 error-body read", async () => {
  const body = deferred<string>();
  const reading = deferred<void>();
  const env = environment(async () => {
    const response = new Response("", { status: 401 });
    response.text = () => { reading.resolve(); return body.promise; };
    return response;
  });
  try {
    const pending = authFetch("/api/synthetic").catch((error: unknown) => error);
    await reading.promise;
    env.replaceOwner();
    body.resolve(errorBody);
    assertSafeError(await pending);
    assert.deepEqual(env.events, []);
  } finally { env.restore(); }
});
