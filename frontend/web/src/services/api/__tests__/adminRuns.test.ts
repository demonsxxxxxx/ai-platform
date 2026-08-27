import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAdminRunsUrl,
  fetchAdminRunDetail,
  fetchAdminRuns,
  type AdminRunsApiClient,
} from "../adminRuns";

test("admin Runs list uses the bounded tenant-scoped administrator endpoint", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminRunsApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return { runs: [], limit: 25 } as T;
    },
  };

  const response = await fetchAdminRuns(25, client);

  assert.equal(buildAdminRunsUrl(25), "/api/ai/admin/runs?limit=25");
  assert.deepEqual(response, { runs: [], limit: 25 });
  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/runs?limit=25",
      init: { method: "GET" },
    },
  ]);
});

test("admin Run detail encodes the Run identity and remains read only", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminRunsApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return {
        run: { run_id: "run/a", session_id: "chat-a", user_id: "user-a", status: "running" },
        events: [],
        steps: [],
        sandbox_leases: [],
      } as T;
    },
  };

  await fetchAdminRunDetail("run/a", client);

  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/runs/run%2Fa",
      init: { method: "GET" },
    },
  ]);
});
