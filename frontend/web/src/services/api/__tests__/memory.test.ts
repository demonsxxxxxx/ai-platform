import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAdminMemoryPoliciesUrl,
  buildMemoryRecordsUrl,
  cleanupExpiredMemoryRecords,
  fetchAdminMemoryPolicies,
  fetchMemoryPolicy,
  fetchMemoryRecords,
  normalizeMemoryRecord,
  setMemoryPolicy,
  type MemoryPolicy,
} from "../memory.ts";

test("buildMemoryRecordsUrl requires a session id and encodes public filters", () => {
  assert.equal(
    buildMemoryRecordsUrl({
      workspace_id: "workspace-a",
      agent_id: "document-review",
      session_id: "session/with spaces",
      limit: 25,
    }),
    "/api/ai/memory/records?workspace_id=workspace-a&agent_id=document-review&session_id=session%2Fwith%20spaces&limit=25",
  );
});

test("buildAdminMemoryPoliciesUrl appends optional admin filters", () => {
  assert.equal(
    buildAdminMemoryPoliciesUrl({
      workspace_id: "workspace-a",
      user_id: "user-a",
      agent_id: "document-review",
      limit: 5,
    }),
    "/api/ai/admin/memory/policies?workspace_id=workspace-a&user_id=user-a&agent_id=document-review&limit=5",
  );
});

test("memory policy and inventory calls use ai-platform public projection endpoints", async () => {
  const calls: Array<{ url: string; method?: string; body?: unknown }> = [];
  const client = {
    request: async <T>(url: string, init?: RequestInit): Promise<T> => {
      calls.push({
        url,
        method: init?.method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });
      if (url.includes("/admin/memory/policies")) {
        return {
          memory_policies: [
            {
              tenant_id: "default",
              workspace_id: "default",
              user_id: "user-a",
              agent_id: "document-review",
              memory_enabled: false,
              long_term_memory_enabled: false,
              retention_days: 30,
              source: "stored",
              reason: "user opt-out",
              updated_by: "user-a",
              updated_at: "2026-06-05T10:00:00Z",
            } satisfies MemoryPolicy,
          ],
          summary: {
            workspace_id: "default",
            returned_count: 1,
            limit: 10,
          },
        } as T;
      }
      return {
        memory_policy: {
          tenant_id: "default",
          workspace_id: "default",
          user_id: "user-a",
          agent_id: "document-review",
          memory_enabled: false,
          long_term_memory_enabled: false,
          retention_days: 30,
          source: "stored",
          reason: "user opt-out",
          updated_by: "user-a",
          updated_at: "2026-06-05T10:00:00Z",
        },
      } as T;
    },
  };

  await fetchMemoryPolicy({ workspace_id: "default" }, client);
  await setMemoryPolicy(
    {
      workspace_id: "default",
      agent_id: "document-review",
      memory_enabled: false,
      long_term_memory_enabled: false,
      retention_days: 30,
      reason: "user opt-out",
    },
    client,
  );
  await fetchAdminMemoryPolicies({ workspace_id: "default", limit: 10 }, client);

  assert.deepEqual(calls, [
    {
      url: "/api/ai/memory/policy?workspace_id=default",
      method: "GET",
      body: undefined,
    },
    {
      url: "/api/ai/memory/policy",
      method: "PUT",
      body: {
        workspace_id: "default",
        agent_id: "document-review",
        memory_enabled: false,
        long_term_memory_enabled: false,
        retention_days: 30,
        reason: "user opt-out",
      },
    },
    {
      url: "/api/ai/admin/memory/policies?workspace_id=default&limit=10",
      method: "GET",
      body: undefined,
    },
  ]);
});

test("memory records and cleanup use only ai-platform memory routes", async () => {
  const calls: Array<{ url: string; method?: string }> = [];
  const client = {
    request: async <T>(url: string, init?: RequestInit): Promise<T> => {
      calls.push({ url, method: init?.method });
      if (url.includes("/admin/memory/retention/cleanup")) {
        return { deleted_count: 0, memory_records: [] } as T;
      }
      return {
        memory_records: [
          {
            memory_record_id: "mem-a",
            tenant_id: "default",
            workspace_id: "default",
            user_id: "user-a",
            agent_id: "document-review",
            session_id: "session-a",
            record_type: "fact",
            content: "safe public memory",
            metadata: { source: "chat" },
            status: "active",
            created_at: "2026-06-05T10:00:00Z",
          },
        ],
      } as T;
    },
  };

  await fetchMemoryRecords(
    {
      workspace_id: "default",
      agent_id: "document-review",
      session_id: "session-a",
      limit: 20,
    },
    client,
  );
  await cleanupExpiredMemoryRecords({ workspace_id: "default", limit: 25 }, client);

  assert.deepEqual(calls, [
    {
      url: "/api/ai/memory/records?workspace_id=default&agent_id=document-review&session_id=session-a&limit=20",
      method: "GET",
    },
    {
      url: "/api/ai/admin/memory/retention/cleanup?workspace_id=default&limit=25",
      method: "POST",
    },
  ]);
});

test("fetchMemoryRecords rejects missing session id before requesting records", async () => {
  let called = false;
  await assert.rejects(
    () =>
      fetchMemoryRecords(
        { workspace_id: "default" },
        {
          request: async <T>(): Promise<T> => {
            called = true;
            return { memory_records: [] } as T;
          },
        },
      ),
    /memory_session_id_required/,
  );
  assert.equal(called, false);
});

test("normalizeMemoryRecord strips executor private payload and secret-like metadata", () => {
  const record = normalizeMemoryRecord({
    memory_record_id: "mem-a",
    tenant_id: "default",
    workspace_id: "default",
    user_id: "user-a",
    agent_id: "document-review",
    session_id: "session-a",
    record_type: "fact",
    content: "safe public memory",
    metadata: {
      source: "chat",
      rawPayload: { secret: true },
      raw_command: "cat /tmp/secret",
      rawCommand: "cat /tmp/secret",
      request_payload: { secret: true },
      private_payload: { secret: true },
      storage_key: "tenant/private",
      runtime_path: "/tmp/private",
      api_key: "sk-secret",
      API_KEY: "sk-secret",
      client_secret: "client-secret",
      bearer_token: "bearer-secret",
    },
    payload: { secret: true },
    executor_private_payload: { secret: true },
    status: "active",
    created_at: "2026-06-05T10:00:00Z",
  });

  assert.deepEqual(record.metadata, { source: "chat" });
  assert.equal(JSON.stringify(record).includes("secret"), false);
  assert.equal(JSON.stringify(record).includes("storage_key"), false);
  assert.equal(JSON.stringify(record).includes("runtime_path"), false);
  assert.equal(JSON.stringify(record).includes("private_payload"), false);
  assert.equal(JSON.stringify(record).includes("raw_command"), false);
  assert.equal(JSON.stringify(record).includes("client_secret"), false);
  assert.equal(JSON.stringify(record).includes("bearer_token"), false);
});
