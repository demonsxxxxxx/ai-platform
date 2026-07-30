import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCapabilityDistributionListUrl,
  buildCapabilityDistributionUrl,
  capabilityDistributionApi,
  normalizeCapabilityDistribution,
} from "../capabilityDistribution.ts";

const projection = {
  id: "dist-1",
  tenant_id: "tenant-1",
  capability_kind: "skill",
  capability_id: "skill/with space",
  status: "active",
  visible_to_user: true,
  scope_mode: "allowlist",
  department_ids: ["engineering"],
  allowed_roles: ["operator"],
  metadata_json: { source: "existing" },
};

test("normalizes only the typed capability-distribution projection", () => {
  assert.deepEqual(normalizeCapabilityDistribution(projection), {
    id: "dist-1",
    tenantId: "tenant-1",
    capabilityKind: "skill",
    capabilityId: "skill/with space",
    status: "active",
    visibleToUser: true,
    scopeMode: "allowlist",
    departmentIds: ["engineering"],
    allowedRoles: ["operator"],
    metadata: { source: "existing" },
  });
  assert.throws(() =>
    normalizeCapabilityDistribution({ ...projection, visible_to_user: "true" }),
  );
});

test("uses the existing admin distribution endpoints and serializes all ACL controls", async () => {
  assert.equal(
    buildCapabilityDistributionListUrl("skill"),
    "/api/admin/capability-distributions?capability_kind=skill",
  );
  assert.equal(
    buildCapabilityDistributionUrl("skill", "skill/with space"),
    "/api/admin/capability-distributions/skill/skill%2Fwith%20space",
  );

  const originalFetch = globalThis.fetch;
  const calls: Array<{ url: string; method?: string; body?: string | null }> = [];
  globalThis.fetch = (async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method,
      body: typeof init?.body === "string" ? init.body : null,
    });
    return new Response(
      JSON.stringify({ capability_distribution: projection }),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    await capabilityDistributionApi.update("skill", "skill/with space", {
      status: "disabled",
      visibleToUser: false,
      scopeMode: "allowlist",
      departmentIds: ["engineering", "finance"],
      allowedRoles: ["operator", "approver"],
      metadata: { source: "existing" },
    });
    assert.deepEqual(calls, [
      {
        url: "/api/admin/capability-distributions/skill/skill%2Fwith%20space",
        method: "PUT",
        body: JSON.stringify({
          status: "disabled",
          visible_to_user: false,
          scope_mode: "allowlist",
          department_ids: ["engineering", "finance"],
          allowed_roles: ["operator", "approver"],
          metadata: { source: "existing" },
        }),
      },
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
