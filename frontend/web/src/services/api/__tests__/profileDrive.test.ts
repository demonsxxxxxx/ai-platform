import assert from "node:assert/strict";
import test from "node:test";

import {
  profileDriveApi,
  ProfileDriveRequestError,
} from "../profileDrive.ts";

test("lists the current user's ProfileDrive directory through the credential handoff", async () => {
  const originalFetch = globalThis.fetch;
  const requests: Array<{ url: string; init: RequestInit }> = [];
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    requests.push({ url, init });
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.endsWith("/api/profile-drive/files/list")) {
      return new Response(
        JSON.stringify({
          status: "success",
          path: "reports",
          entries: [
            {
              path: "reports/report.pdf",
              name: "report.pdf",
              type: "file",
              size: 42,
              lastModifiedUtc: "2026-09-21T00:00:00Z",
            },
          ],
          truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    throw new Error(`Unexpected request: ${url}`);
  }) as typeof fetch;

  try {
    const result = await profileDriveApi.listFiles("reports");

    assert.equal(result.entries[0]?.path, "reports/report.pdf");
    assert.equal(requests.length, 2);
    assert.equal(
      new Headers(requests[1]?.init.headers).get("Authorization"),
      "Bearer handoff-jwt",
    );
    assert.equal(requests[1]?.init.credentials, "omit");
    assert.deepEqual(JSON.parse(String(requests[1]?.init.body)), {
      source: "profile",
      path: "reports",
      maxEntries: 200,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("selects the fixed public drive source without accepting a server", async () => {
  const originalFetch = globalThis.fetch;
  const bodies: unknown[] = [];
  globalThis.fetch = (async (input, init = {}) => {
    const url = String(input);
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    bodies.push(JSON.parse(String(init.body)));
    return new Response(
      JSON.stringify({
        status: "success",
        path: "",
        entries: [],
        truncated: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    await profileDriveApi.listFiles("", "public");
    assert.deepEqual(bodies, [{ source: "public", path: "", maxEntries: 200 }]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects malformed ProfileDrive directory projections", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input) => {
    const url = String(input);
    if (url.endsWith("/api/ai/auth/company-credential-handoff")) {
      return new Response(JSON.stringify({ credential: "handoff-jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(
      JSON.stringify({
        status: "success",
        path: "",
        entries: [{ path: "private", name: "private", type: "socket" }],
        truncated: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;

  try {
    await assert.rejects(
      profileDriveApi.listFiles(""),
      (error: unknown) =>
        error instanceof ProfileDriveRequestError &&
        error.code === "invalid_response",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
