import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAdminRunsUrl,
  exportAdminRunDiagnostics,
  fetchAdminRunDiagnostics,
  fetchAdminRunDetail,
  fetchAdminRunTrajectory,
  fetchAdminRuns,
  fetchAdminRunMonitor,
  fetchAdminRunInvestigation,
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

test("admin Run trajectory reads a bounded cursor page and rejects a mismatched Run", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminRunsApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return {
        contract_version: "ai-platform.admin-run-trajectory.v1",
        run_id: "run/a",
        after_sequence: 10,
        next_after_sequence: 12,
        has_more: false,
        source_count: 2,
        omitted: { private: 0, unsupported: 0, invalid: 0 },
        events: [],
      } as T;
    },
  };

  const page = await fetchAdminRunTrajectory("run/a", 10, client);
  assert.equal(page.next_after_sequence, 12);
  assert.deepEqual(calls, [{
    url: "/api/ai/admin/runs/run%2Fa/trajectory?after_sequence=10&limit=100",
    init: { method: "GET" },
  }]);
  await assert.rejects(fetchAdminRunTrajectory("run/b", 10, client), /admin_run_trajectory_response_invalid/);
});

test("admin Runs list binds a user scope for deep-linked diagnostics", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminRunsApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return { runs: [], limit: 50 } as T;
    },
  };

  await fetchAdminRuns({ limit: 50, userId: "user/a", status: "failed" }, client);

  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/runs?limit=50&user_id=user%2Fa&status=failed",
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

test("admin Run diagnostics uses the independent encoded read endpoint", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const client: AdminRunsApiClient = {
    async request<T>(url: string, init?: RequestInit): Promise<T> {
      calls.push({ url, init });
      return {
        schema_version: "ai-platform.run-diagnostics.v1",
        revision: 0,
        coverage: "not_collected",
        run: { run_id: "run/a", session_id: "chat-a", user_id: "user-a", status: "running" },
        handling: [],
        losses: [],
        attempts: [],
        details: { sdk: {}, tool_lifecycles: [], tool_calls: [], tool_policy_denials: [] },
        versions: {},
        counts: { retained_observations: 0, omitted_observations: 0 },
      } as T;
    },
  };

  await fetchAdminRunDiagnostics("run/a", client);

  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/runs/run%2Fa/diagnostics",
      init: { method: "GET" },
    },
  ]);
});

test("admin Run diagnostics rejects an empty response instead of rendering a blank panel", async () => {
  const client: AdminRunsApiClient = {
    async request<T>(): Promise<T> {
      return null as T;
    },
  };

  await assert.rejects(
    fetchAdminRunDiagnostics("run-a", client),
    /admin_run_diagnostics_response_invalid/,
  );
});

test("admin Run diagnostic export uses an authenticated POST and returns a bounded filename", async () => {
  const originalFetch = globalThis.fetch;
  const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];
  globalThis.fetch = async (input, init) => {
    calls.push({ input, init });
    return new Response(new Blob(["zip-body"]), {
      status: 200,
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": "attachment; filename*=UTF-8''run-diagnostics-run_a.zip",
        "X-Diagnostic-Export-Id": "rdiagexp-a",
      },
    });
  };

  try {
    const result = await exportAdminRunDiagnostics("run/a");
    assert.equal(result.filename, "run-diagnostics-run_a.zip");
    assert.equal(result.exportId, "rdiagexp-a");
    assert.equal(await result.blob.text(), "zip-body");
    assert.equal(calls[0]?.input, "/api/ai/admin/runs/run%2Fa/diagnostic-exports");
    assert.equal(calls[0]?.init?.method, "POST");
    assert.equal(calls[0]?.init?.credentials, "include");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Run performance queries pin the window and frozen model scope and reject old event metrics", async () => {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  let valid = true;
  const client: AdminRunsApiClient = { async request<T>(url: string, init?: RequestInit): Promise<T> {
    calls.push({ url, init });
    return { measurement: valid ? "run_lifecycle.v1" : "event_latency", runs: [], models: [], window: {}, counts: {}, latency: {} } as T;
  } };
  await fetchAdminRunMonitor({ hours: 168, fromAt: "2026-04-01T00:00:00Z", toAt: "2026-04-02T00:00:00Z", agentId: "agent/a", modelId: "model-a", modelGatewayRevision: 3, status: "failed", order: "slow", limit: 50 }, client);
  const url = new URL(calls[0].url, "http://localhost");
  assert.equal(url.pathname, "/api/ai/admin/runs/monitor");
  assert.deepEqual(Object.fromEntries(url.searchParams), { hours: "168", from_at: "2026-04-01T00:00:00Z", to_at: "2026-04-02T00:00:00Z", agent_id: "agent/a", model_id: "model-a", model_gateway_revision: "3", status: "failed", order: "slow", limit: "50" });
  assert.equal(calls[0].init?.method, "GET");
  valid = false;
  await assert.rejects(fetchAdminRunMonitor({}, client), /admin_run_monitor_response_invalid/);
});

test("Run investigation encodes identity and rejects another Run's evidence", async () => {
  const calls: string[] = [];
  const client: AdminRunsApiClient = { async request<T>(url: string): Promise<T> {
    calls.push(url);
    return { run_id: "run/a", timeline_source: "platform_receipts", stages: [] } as T;
  } };
  await fetchAdminRunInvestigation("run/a", client);
  assert.equal(calls[0], "/api/ai/admin/runs/run%2Fa/investigation");
  await assert.rejects(fetchAdminRunInvestigation("run/b", client), /admin_run_investigation_response_invalid/);
});
