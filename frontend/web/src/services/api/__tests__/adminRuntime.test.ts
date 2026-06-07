import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { getAdminRuntimeOverview } from "../adminRuntime.ts";

const apiSource = readFileSync(
  join(import.meta.dirname, "../adminRuntime.ts"),
  "utf8",
);

test("fetches admin runtime overview from the ai-platform admin projection", async () => {
  const calls: Array<{ url: string; method?: string }> = [];

  const overview = await getAdminRuntimeOverview({
    request: async <T>(url: string, init?: RequestInit): Promise<T> => {
      calls.push({ url, method: init?.method });
      return {
        tenant_id: "tenant-a",
        capacity: {
          schema_version: "ai-platform.capacity-baseline.v1",
          profile: "unproven_default",
          limits: {
            worker: { max_active_worker_runs: 3 },
            database_pool: { max_size: 10 },
            queue: { tenant_processing_quota_enabled: false },
          },
          warnings: ["queue_tenant_processing_quota_disabled"],
          production_default_policy:
            "do_not_raise_without_recorded_load_test_evidence",
        },
        backpressure: {
          reasons: ["worker_capacity_saturated"],
          queue: {
            worker_capacity: {
              max_active_worker_runs: 3,
              available_worker_slots: 0,
              processing_saturated: true,
            },
          },
          database_pool: {
            open: true,
            requests_waiting: 0,
            max_waiting: 100,
            waiting_saturated: false,
          },
        },
        governance: {
          schema_version: "ai-platform.governance-readiness.v1",
          status: "partial_blocked",
          open_gaps: ["admin_runtime_governance_visual_acceptance"],
          domains: {
            frontend_projection: {
              status: "partial_blocked",
              gaps: ["admin_runtime_governance_visual_acceptance"],
            },
          },
        },
      } as T;
    },
  });

  assert.deepEqual(calls, [
    {
      url: "/api/ai/admin/runtime/overview",
      method: undefined,
    },
  ]);
  assert.equal(overview.capacity?.schema_version, "ai-platform.capacity-baseline.v1");
  assert.equal(overview.governance?.status, "partial_blocked");
  assert.deepEqual(overview.backpressure?.reasons, ["worker_capacity_saturated"]);
});

test("admin runtime API source only references public or admin projection fields", () => {
  const forbiddenTerms = [
    "executor" + "PrivatePayload",
    "executor_" + "private_payload",
    "raw_" + "payload",
    "storage" + "Key",
    "storage_" + "key",
    "sandbox" + "Workdir",
    "sandbox_" + "workdir",
    "work" + "Dir",
    "work_" + "dir",
    "API" + "_KEY",
    "api" + "_key",
  ];
  assert.match(apiSource, /\/api\/ai\/admin\/runtime\/overview/);

  for (const term of forbiddenTerms) {
    assert.ok(!apiSource.includes(term), `source includes ${term}`);
  }
});
