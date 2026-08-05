import assert from "node:assert/strict";
import test from "node:test";

import type { DepartmentDirectoryNode } from "../../../services/api/capabilityDistribution.ts";
import {
  flattenDepartmentDirectory,
  resolveDepartmentSelection,
} from "../departmentDirectorySelection.ts";

const directory: DepartmentDirectoryNode[] = [
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
        selectable: true,
        reason: null,
        children: [],
      },
      {
        directoryId: "3",
        authorityId: "QA",
        name: "QA",
        path: "总部 / QA",
        selectable: false,
        reason: "duplicate_authority_id",
        children: [],
      },
    ],
  },
];

test("flattens the tree without replacing exact authority labels", () => {
  assert.deepEqual(
    flattenDepartmentDirectory(directory).map(({ authorityId, depth }) => ({
      authorityId,
      depth,
    })),
    [
      { authorityId: "总部", depth: 0 },
      { authorityId: "研发", depth: 1 },
      { authorityId: "QA", depth: 1 },
    ],
  );
});

test("accepts only exact selectable authorities and preserves stale values", () => {
  assert.deepEqual(resolveDepartmentSelection(["研发"], directory), {
    resolved: [flattenDepartmentDirectory(directory)[1]],
    unresolvedAuthorityIds: [],
    authoritative: true,
  });
  assert.deepEqual(resolveDepartmentSelection(["研发 ", "QA", "未知"], directory), {
    resolved: [],
    unresolvedAuthorityIds: ["研发 ", "QA", "未知"],
    authoritative: false,
  });
  assert.deepEqual(resolveDepartmentSelection(["研发"], null), {
    resolved: [],
    unresolvedAuthorityIds: ["研发"],
    authoritative: false,
  });
});

test("allows a fail-closed clear while directory authority is unavailable", () => {
  assert.deepEqual(resolveDepartmentSelection([], null), {
    resolved: [],
    unresolvedAuthorityIds: [],
    authoritative: true,
  });
});
