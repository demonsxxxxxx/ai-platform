import assert from "node:assert/strict";
import test from "node:test";

import {
  fetchBrowserRuntimeConfig,
  normalizeBrowserRuntimeConfig,
} from "../browserRuntimeConfig.ts";

const validProjection = () => ({
  launchpad_urls: {
    lingxi: "http://10.56.0.25:8189/#/TaskManagement/indexSpace/",
    sop_assistant: "https://apps.example.test/#/AI/RAGFlowSOP",
    word_translate: null,
    word_review: "https://apps.example.test/#/AI/WordReview",
  },
});

test("normalizes the exact allowlisted browser runtime projection", () => {
  assert.deepEqual(normalizeBrowserRuntimeConfig(validProjection()), {
    launchpadUrls: validProjection().launchpad_urls,
  });
});

test("rejects extra private fields and unsafe browser destination URLs", () => {
  const invalid: unknown[] = [
    { ...validProjection(), database_url: "postgresql://private" },
    {
      launchpad_urls: {
        ...validProjection().launchpad_urls,
        existing_auth_base_url: "https://auth.internal.example",
      },
    },
    {
      launchpad_urls: {
        ...validProjection().launchpad_urls,
        lingxi: "javascript:alert(1)",
      },
    },
    {
      launchpad_urls: {
        ...validProjection().launchpad_urls,
        lingxi: "https://user:password@example.test/path",
      },
    },
    {
      launchpad_urls: {
        ...validProjection().launchpad_urls,
        lingxi: "https://example.test/path?token=secret",
      },
    },
    {
      launchpad_urls: {
        ...validProjection().launchpad_urls,
        lingxi: "https://example.test/#access_token=secret",
      },
    },
  ];

  for (const projection of invalid) {
    assert.throws(
      () => normalizeBrowserRuntimeConfig(projection),
      /browser_runtime_config_invalid/,
    );
  }
});

test("fetches browser runtime config from the same-origin no-store endpoint", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{
    url: string;
    cache?: RequestCache;
    credentials?: RequestCredentials;
  }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      cache: init?.cache,
      credentials: init?.credentials,
    });
    return new Response(JSON.stringify(validProjection()), { status: 200 });
  }) as typeof fetch;

  try {
    assert.deepEqual(await fetchBrowserRuntimeConfig(), {
      launchpadUrls: validProjection().launchpad_urls,
    });
    assert.deepEqual(calls, [
      {
        url: "/api/runtime-config/browser",
        cache: "no-store",
        credentials: "include",
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
