import test from "node:test";
import assert from "node:assert/strict";

import { ApiRequestError, authFetch } from "../fetch.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";

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

test("authFetch uses cookie credentials and never sends legacy bearer headers", async () => {
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
    assert.deepEqual(stubs.removedKeys, ["access_token", "refresh_token"]);
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

test("authFetch exposes only the safe server status and detail code to governance clients", async () => {
  const stubs = installFetchAuthStubs({
    fetchImpl: async () =>
      new Response(
        JSON.stringify({ detail: "tool_permission_decision_not_supported" }),
        {
          status: 409,
          headers: { "Content-Type": "application/json" },
        },
      ),
  });

  try {
    await assert.rejects(
      () => authFetch("/api/ai/tool-permissions/inbox/request/decision"),
      (error: unknown) => {
        assert.equal(error instanceof ApiRequestError, true);
        assert.equal((error as ApiRequestError).status, 409);
        assert.equal(
          (error as ApiRequestError).code,
          "tool_permission_decision_not_supported",
        );
        assert.doesNotMatch((error as Error).message, /private|token/i);
        return true;
      },
    );
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

test("authFetch treats force-relogin as a typed safe error without global side effects", async () => {
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
    assert.deepEqual(stubs.events, []);
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

test("authFetch does not log raw response bodies when JSON parsing fails", async () => {
  const originalWarn = console.warn;
  const warnings: unknown[][] = [];
  const stubs = installFetchAuthStubs({
    fetchImpl: async () =>
      new Response("secret=backend-cookie", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  });
  console.warn = (...args: unknown[]) => {
    warnings.push(args);
  };

  try {
    await authFetch("/api/sessions");
    assert.deepEqual(warnings, [["[authFetch] Failed to parse response as JSON"]]);
  } finally {
    console.warn = originalWarn;
    stubs.restore();
  }
});
