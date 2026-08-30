import assert from "node:assert/strict";
import test from "node:test";

import { knowledgeApi } from "../knowledge";

test("Knowledge connection writes a write-only credential without browser tenant scope", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; body: Record<string, unknown> }> = [];
  globalThis.fetch = async (input, init) => {
    calls.push({
      url: String(input),
      body: JSON.parse(String(init?.body)) as Record<string, unknown>,
    });
    return new Response(
      JSON.stringify({
        id: "knc_a",
        name: "制度库",
        provider_key: "ragflow",
        base_url: "https://ragflow.example",
        status: "draft",
        lifecycle_epoch: 0,
        credential_state: "configured",
        credential_fingerprint: "0123456789abcdef",
        candidate_revision_id: "knr_a",
        active_revision_id: null,
        active_catalog_sync_id: null,
        last_authenticated_check_at: null,
        last_complete_sync_at: null,
        safe_failure_code: null,
        source_count: 0,
        created_at: null,
        updated_at: null,
      }),
      { status: 201, headers: { "content-type": "application/json" } },
    );
  };
  try {
    const result = await knowledgeApi.createConnection({
      name: "制度库",
      base_url: "https://ragflow.example",
      credential: "write-only-key",
    });

    assert.equal(result.credential_state, "configured");
    assert.match(calls[0]!.url, /\/api\/ai\/admin\/knowledge\/connections$/);
    assert.equal(calls[0]!.body.credential, "write-only-key");
    assert.equal(typeof calls[0]!.body.operation_id, "string");
    assert.equal("tenant_id" in calls[0]!.body, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Knowledge list keeps provider cursors out of browser query contracts", async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = async (input) => {
    urls.push(String(input));
    return new Response(
      JSON.stringify({
        items: [
          {
            id: "ks_finance",
            connection_id: "knc_a",
            connection_name: "公司 RAGFlow",
            name: "财务制度",
            provider_name: "finance-dataset",
            description: "已治理的知识源",
            status: "active",
            authorization_version: 3,
            visibility: "restricted",
            allowed_department_ids: ["finance"],
            allowed_roles: [],
            allowed_user_ids: [],
            first_seen_at: "2026-08-30T00:00:00Z",
            last_seen_at: "2026-08-30T01:00:00Z",
            last_complete_sync_at: "2026-08-30T01:00:00Z",
            connection_status: "active",
          },
        ],
        next_cursor: null,
        limit: 20,
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };
  try {
    const page = await knowledgeApi.listSources({
      cursor: "opaque-platform-cursor",
      q: "制度",
      connectionId: "knc/a",
      status: "active",
    });

    assert.match(urls[0]!, /cursor=opaque-platform-cursor/);
    assert.match(urls[0]!, /connection_id=knc%2Fa/);
    assert.doesNotMatch(urls[0]!, /provider_cursor|dataset_id|tenant_id/);
    assert.equal(page.items[0]?.last_complete_sync_at, "2026-08-30T01:00:00Z");
    assert.equal(page.items[0]?.connection_status, "active");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Agent Builder reads the server-governed Knowledge catalog", async () => {
  const originalFetch = globalThis.fetch;
  const urls: string[] = [];
  globalThis.fetch = async (input) => {
    urls.push(String(input));
    return new Response(
      JSON.stringify({
        sources: [
          {
            id: "ks_finance",
            name: "财务制度",
            description: "已治理的知识源",
            authorization_version: 3,
            connection_name: "公司 RAGFlow",
            last_seen_at: null,
            available: true,
            source_status: "active",
            connection_status: "active",
            visibility: "restricted",
            allowed_department_count: 1,
            allowed_department_ids: ["finance"],
            allowed_roles: [],
            allowed_user_ids: [],
          },
        ],
        next_cursor: null,
        limit: 50,
        retrieval_profiles: [
          {
            id: "krp_default",
            revision: 1,
            name: "标准检索",
            description: "确定性检索策略",
            status: "active",
            content_hash: "a".repeat(64),
          },
        ],
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };
  try {
    const catalog = await knowledgeApi.builderCatalog({
      q: "财务",
      selectedSourceIds: ["ks_finance"],
    });

    assert.match(urls[0]!, /^\/api\/ai\/admin\/knowledge\/builder-catalog\?/);
    assert.match(urls[0]!, /q=%E8%B4%A2%E5%8A%A1/);
    assert.match(urls[0]!, /selected_source_id=ks_finance/);
    assert.deepEqual(catalog.sources.map((source) => source.id), ["ks_finance"]);
    assert.deepEqual(catalog.retrieval_profiles.map((profile) => profile.id), [
      "krp_default",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
