import assert from "node:assert/strict";
import test from "node:test";
import { modelGroupsApi } from "../modelGroups";
import { companyEmployeesApi } from "../companyEmployees";

test("model group mutations use dedicated atomic endpoints and preserve abort ownership", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify({ tenant_id: "tenant-a", operation: "add", preview: true, changed: true, groups: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const controller = new AbortController();
    await modelGroupsApi.setModels("g/sales", {
      revision: 7,
      models: [{ model_id: "mdl-a", enabled: true, display_order: 0, priority: 20, weight: 100 }],
    }, { signal: controller.signal });
    await modelGroupsApi.batchModels({
      group_ids: ["g_sales"], model_ids: ["mdl-a"], operation: "add",
      expected_group_revisions: { "g_sales": 7 }, preview: true,
    }, { signal: controller.signal });
    await modelGroupsApi.setUserMemberships("user/a", {
      group_ids: ["g_sales"],
      primary_group_id: "g_sales",
      expected_revision: 3,
      confirm_default_fallback: false,
    });
    assert.equal(calls[0].url, "/api/ai/admin/model-groups/g%2Fsales/models");
    assert.equal(calls[0].init?.method, "PUT");
    assert.equal(calls[0].init?.signal, controller.signal);
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      revision: 7,
      models: [{ model_id: "mdl-a", enabled: true, display_order: 0, priority: 20, weight: 100 }],
    });
    assert.equal(calls[1].url, "/api/ai/admin/model-groups/models/batch");
    assert.deepEqual(JSON.parse(String(calls[1].init?.body)), {
      group_ids: ["g_sales"], model_ids: ["mdl-a"], operation: "add",
      expected_group_revisions: { "g_sales": 7 }, preview: true,
    });
    assert.equal(calls[2].url, "/api/ai/admin/users/user%2Fa/model-groups");
    assert.deepEqual(JSON.parse(String(calls[2].init?.body)), {
      group_ids: ["g_sales"],
      primary_group_id: "g_sales",
      expected_revision: 3,
      confirm_default_fallback: false,
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("employee picker scopes search and pagination to an exact department and forwards cancellation", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; signal?: AbortSignal | null }> = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ url: String(input), signal: init?.signal });
    return new Response(JSON.stringify({ employees: [], total: 0, offset: 100, limit: 100 }), { status: 200 });
  };
  try {
    const controller = new AbortController();
    await companyEmployeesApi.list({ departmentId: "123", search: " 员工 A ", offset: 100, limit: 100, signal: controller.signal });
    const url = new URL(calls[0].url, "https://synthetic.test");
    assert.equal(url.searchParams.get("department_id"), "123");
    assert.equal(url.searchParams.get("search"), "员工 A");
    assert.equal(url.searchParams.get("offset"), "100");
    assert.equal(calls[0].signal, controller.signal);
  } finally { globalThis.fetch = originalFetch; }
});

test("public model listing scopes the selected group without leaking admin fields", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return new Response(JSON.stringify({ models: [], count: 0, enabled_count: 0 }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    await modelGroupsApi.listPublic();
    const { modelPublicApi } = await import("../modelPublic");
    await modelPublicApi.listAvailable({ groupId: "g sales" });
    assert.deepEqual(calls, ["/api/ai/models/groups", "/api/agent/models/available?group_id=g%20sales"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
