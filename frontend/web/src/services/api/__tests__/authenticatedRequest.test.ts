import test from "node:test";
import assert from "node:assert/strict";

import { authenticatedRequest } from "../authenticatedRequest.ts";
import { registerAuthScopedCacheClearer } from "../authCacheInvalidation.ts";
import { ApiRequestError } from "../fetch.ts";

function installAuthenticatedRequestStubs(
  fetchImpl: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>,
) {
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
  const localStore = new Map<string, string>([
    ["ai_platform_session_present", "session-marker"],
  ]);
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
      getItem: (key: string) => localStore.get(key) ?? null,
      setItem: (key: string, value: string) => localStore.set(key, value),
      removeItem: (key: string) => {
        removedKeys.push(key);
        localStore.delete(key);
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
    events,
    localStore,
    removedKeys,
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

test("authenticatedRequest strips caller Authorization headers in browser mode", async () => {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const stubs = installAuthenticatedRequestStubs(async (input, init) => {
    calls.push({ input: String(input), init });
    return new Response("ok", { status: 200 });
  });

  try {
    const response = await authenticatedRequest("/api/sessions", {
      headers: {
        Authorization: "Bearer leaked-token",
        "X-Test": "1",
      },
    });

    assert.equal(response.status, 200);
    const headers = new Headers(calls[0].init?.headers);
    assert.equal(headers.has("Authorization"), false);
    assert.equal(headers.get("X-Test"), "1");
    assert.equal(calls[0].init?.credentials, "include");
  } finally {
    stubs.restore();
  }
});

test("authenticatedRequest never replays a stale POST or mutates replacement auth state", async () => {
  const calls: Array<{ url: string; body: BodyInit | null | undefined }> = [];
  const stubs = installAuthenticatedRequestStubs(async (input, init) => {
    calls.push({ url: String(input), body: init?.body });
    localStorage.setItem("ai_platform_session_present", "marker-b");
    return new Response(JSON.stringify({ detail: "unauthorized" }), {
      status: 401,
      headers: { "Content-Type": "application/json" },
    });
  });
  let cacheClears = 0;
  const unregister = registerAuthScopedCacheClearer(() => {
    cacheClears += 1;
  });

  try {
    await assert.rejects(
      () =>
        authenticatedRequest("/api/sessions", {
          method: "POST",
          body: JSON.stringify({ value: "must-not-replay" }),
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
        body: JSON.stringify({ value: "must-not-replay" }),
      },
    ]);
    assert.equal(stubs.localStore.get("ai_platform_session_present"), "marker-b");
    assert.deepEqual(stubs.events, []);
    assert.equal(stubs.sessionStore.size, 0);
    assert.equal(cacheClears, 0);
    assert.deepEqual(stubs.removedKeys, []);
  } finally {
    unregister();
    stubs.restore();
  }
});

test("authenticatedRequest treats force-relogin as a typed safe error without side effects", async () => {
  const calls: string[] = [];
  const stubs = installAuthenticatedRequestStubs(async (input) => {
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
  });

  try {
    await assert.rejects(
      () => authenticatedRequest("/api/sessions"),
      (error: unknown) => {
        assert.equal(error instanceof ApiRequestError, true);
        assert.equal((error as ApiRequestError).status, 401);
        assert.doesNotMatch((error as Error).message, /proxy|token|private/i);
        return true;
      },
    );
    assert.deepEqual(calls, ["/api/sessions"]);
    assert.equal(
      stubs.localStore.get("ai_platform_session_present"),
      "session-marker",
    );
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
  }
});

test("authenticatedRequest preserves AbortError identity", async () => {
  const abort = new DOMException("aborted", "AbortError");
  const stubs = installAuthenticatedRequestStubs(async () => {
    throw abort;
  });

  try {
    await assert.rejects(
      () => authenticatedRequest("/api/sessions"),
      (error: unknown) => error === abort,
    );
  } finally {
    stubs.restore();
  }
});
