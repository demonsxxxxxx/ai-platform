import assert from "node:assert/strict";
import test from "node:test";
import { modelAdminApi, type AdminModelEntry } from "../modelAdmin";

test("model publication omits retired global default and per-model ACL fields", async () => {
  const originalFetch = globalThis.fetch;
  let body: Record<string, unknown> | null = null;
  globalThis.fetch = async (_input, init) => {
    body = JSON.parse(String(init?.body));
    return new Response(JSON.stringify({ connection: { configured: true, revision: 2, base_url: "https://gateway.test", key_fingerprint: "synthetic" }, models: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const model: AdminModelEntry = { id: "model-a", value: "provider/model-a", label: "Model A", provider: "test", enabled: true, available: true, order: 1, last_seen_revision: 2, last_seen_at: "2026-01-01T00:00:00Z", auto_compact_window: 200000, max_output_tokens: 2048 };
    await modelAdminApi.publish("https://gateway.test", undefined, 1, [model]);
    assert.ok(body);
    const sent = (body as { models: Array<Record<string, unknown>> }).models[0];
    assert.deepEqual(sent, { id: "model-a", value: "provider/model-a", display_name: "Model A", enabled: true, order: 1, auto_compact_window: 200000, max_output_tokens: 2048 });
    assert.equal("is_default" in sent, false);
    assert.equal("access_mode" in sent, false);
    assert.equal("allowed_user_ids" in sent, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
