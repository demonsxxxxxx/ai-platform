import test from "node:test";
import assert from "node:assert/strict";

import { authFetch } from "../fetch.ts";

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
      getItem: () => null,
      setItem: () => {},
      removeItem: () => {},
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

test("authFetch clears browser auth state after cookie session revocation returns 401", async () => {
  const calls: string[] = [];
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "session-marker",
    },
    fetchImpl: async (input) => {
      calls.push(String(input));
      return new Response(JSON.stringify({ detail: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  try {
    await assert.rejects(() => authFetch("/api/sessions"), /Unauthorized/);
    assert.deepEqual(calls, ["/api/sessions", "/api/ai/auth/me"]);
    assert.deepEqual(stubs.events, ["auth:logout"]);
    assert.deepEqual(stubs.removedKeys, ["ai_platform_session_present"]);
  } finally {
    stubs.restore();
  }
});

test("authFetch retries once when the cookie session probe succeeds after a 401", async () => {
  const calls: string[] = [];
  const stubs = installFetchAuthStubs({
    initialLocalStorage: {
      ai_platform_session_present: "session-marker",
    },
    fetchImpl: async (input) => {
      const url = String(input);
      calls.push(url);
      if (calls.length === 1) {
        return new Response(JSON.stringify({ detail: "unauthorized" }), {
          status: 401,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url === "/api/ai/auth/me") {
        return new Response(
          JSON.stringify({
            user_id: "dev001",
            user_name: "dev001",
            display_name: "Dev",
            tenant_id: "default",
            roles: ["user"],
            permissions: ["agent:use"],
            is_admin: false,
            source: "company-login",
          }),
          {
            status: 200,
            headers: { "Content-Type": "application/json" },
          },
        );
      }
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  });

  try {
    const response = await authFetch<{ ok: boolean }>("/api/sessions");

    assert.deepEqual(response, { ok: true });
    assert.deepEqual(calls, ["/api/sessions", "/api/ai/auth/me", "/api/sessions"]);
    assert.deepEqual(stubs.events, []);
  } finally {
    stubs.restore();
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
