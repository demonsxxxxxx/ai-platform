import assert from "node:assert/strict";
import test from "node:test";

import {
  buildCapabilityDistributionListUrl,
  buildCapabilityDistributionUrl,
  buildDepartmentDirectoryUrl,
  capabilityDistributionApi,
  normalizeCapabilityDistribution,
  normalizeDepartmentDirectory,
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

const departmentNode = {
  directory_id: "1",
  authority_id: "总部",
  name: "总部",
  path: "总部",
  selectable: true,
  reason: null,
  children: [] as unknown[],
};

const directoryResponse = () => ({
  departments: [structuredClone(departmentNode)],
});

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

test("normalizes the exact bounded department directory projection", () => {
  const response = directoryResponse();
  response.departments[0].children = [
    {
      directory_id: "2",
      authority_id: "研发",
      name: "研发",
      path: "总部 / 研发",
      selectable: false,
      reason: "duplicate_authority_id",
      children: [],
    },
  ];

  assert.deepEqual(normalizeDepartmentDirectory(response), [
    {
      directoryId: "1",
      authorityId: "总部",
      name: "总部",
      path: "总部",
      selectable: true,
      reason: null,
      children: [
        {
          directoryId: "2",
          authorityId: "研发",
          name: "研发",
          path: "总部 / 研发",
          selectable: false,
          reason: "duplicate_authority_id",
          children: [],
        },
      ],
    },
  ]);
});

test("rejects hostile department keys, identifiers, labels, and integrity drift", () => {
  const hostile: unknown[] = [
    { ...directoryResponse(), source: "private-upstream" },
    { departments: [{ ...departmentNode, employee_count: 12 }] },
    {
      departments: [
        {
          directory_id: "1",
          authority_id: "总部",
          name: "总部",
          path: "总部",
          selectable: true,
          children: [],
        },
      ],
    },
    { departments: [{ ...departmentNode, directory_id: "dept-1" }] },
    { departments: [{ ...departmentNode, authority_id: " 总部", name: " 总部", path: " 总部" }] },
    {
      departments: [
        {
          ...departmentNode,
          authority_id: "x".repeat(161),
          name: "x".repeat(161),
          path: "x".repeat(161),
        },
      ],
    },
    { departments: [{ ...departmentNode, authority_id: "总部\u0000", name: "总部\u0000", path: "总部\u0000" }] },
    { departments: [{ ...departmentNode, name: "总部别名" }] },
    { departments: [{ ...departmentNode, path: "错误 / 总部" }] },
    { departments: [{ ...departmentNode, selectable: false, reason: null }] },
    { departments: [{ ...departmentNode, selectable: true, reason: "duplicate_authority_id" }] },
    {
      departments: [
        { ...departmentNode, children: [{ ...departmentNode, path: "总部 / 总部" }] },
      ],
    },
  ];

  for (const value of hostile) {
    assert.throws(() => normalizeDepartmentDirectory(value));
  }
});

test("rejects department depth and node-count overflow", () => {
  const root = structuredClone(departmentNode);
  let current = root;
  for (let depth = 2; depth <= 13; depth += 1) {
    const child = {
      ...structuredClone(departmentNode),
      directory_id: String(depth),
      authority_id: `部门${depth}`,
      name: `部门${depth}`,
      path: `${current.path} / 部门${depth}`,
    };
    current.children = [child];
    current = child;
  }
  assert.throws(() => normalizeDepartmentDirectory({ departments: [root] }));

  const departments = Array.from({ length: 5_001 }, (_, index) => ({
    ...structuredClone(departmentNode),
    directory_id: String(index + 1),
    authority_id: `部门${index + 1}`,
    name: `部门${index + 1}`,
    path: `部门${index + 1}`,
  }));
  assert.throws(() => normalizeDepartmentDirectory({ departments }));
});

test("fetches the same-origin admin department projection through the typed client", async () => {
  assert.equal(
    buildDepartmentDirectoryUrl(),
    "/api/admin/capability-distributions/department-directory",
  );
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = (async (input) => {
    calls.push(String(input));
    return new Response(JSON.stringify(directoryResponse()), { status: 200 });
  }) as typeof fetch;

  try {
    assert.deepEqual(await capabilityDistributionApi.departmentDirectory(), [
      {
        directoryId: "1",
        authorityId: "总部",
        name: "总部",
        path: "总部",
        selectable: true,
        reason: null,
        children: [],
      },
    ]);
    assert.deepEqual(calls, ["/api/admin/capability-distributions/department-directory"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
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
