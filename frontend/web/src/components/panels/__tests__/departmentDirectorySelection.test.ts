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
    authorityId: "1",
    name: "总部",
    path: "总部",
    selectable: true,
    reason: null,
    children: [
      {
        directoryId: "2",
        authorityId: "2",
        name: "研发",
        path: "总部 / 研发",
        selectable: true,
        reason: null,
        children: [],
      },
      {
        directoryId: "3",
        authorityId: "3",
        name: "QA",
        path: "总部 / QA",
        selectable: false,
        reason: "duplicate_authority_id",
        children: [],
      },
    ],
  },
];

test("flattens the tree without replacing canonical department IDs", () => {
  assert.deepEqual(
    flattenDepartmentDirectory(directory).map(({ authorityId, depth }) => ({
      authorityId,
      depth,
    })),
    [
      { authorityId: "1", depth: 0 },
      { authorityId: "2", depth: 1 },
      { authorityId: "3", depth: 1 },
    ],
  );
});

test("accepts only exact selectable authorities and preserves stale values", () => {
  assert.deepEqual(resolveDepartmentSelection(["2"], directory), {
    resolved: [flattenDepartmentDirectory(directory)[1]],
    unresolvedAuthorityIds: [],
    authoritative: true,
  });
  assert.deepEqual(resolveDepartmentSelection(["2 ", "3", "研发", "未知"], directory), {
    resolved: [],
    unresolvedAuthorityIds: ["2 ", "3", "研发", "未知"],
    authoritative: false,
  });
  assert.deepEqual(resolveDepartmentSelection(["2"], null), {
    resolved: [],
    unresolvedAuthorityIds: ["2"],
    authoritative: false,
  });
});

test("same display names remain separate IDs and legacy names never map automatically", () => {
  const options: DepartmentDirectoryNode[] = ["101", "201"].map((id) => ({
    directoryId: id, authorityId: id, name: "研发", path: `公司 ${id} / 研发`,
    selectable: true, reason: null, children: [],
  }));
  assert.equal(resolveDepartmentSelection(["101", "201"], options).authoritative, true);
  assert.equal(resolveDepartmentSelection(["研发"], options).authoritative, false);
  assert.equal(resolveDepartmentSelection(["101"], [{ ...options[0], directoryId: "201" }]).authoritative, false);
});

test("allows a fail-closed clear while directory authority is unavailable", () => {
  assert.deepEqual(resolveDepartmentSelection([], null), {
    resolved: [],
    unresolvedAuthorityIds: [],
    authoritative: true,
  });
});

test("rejects duplicate selected authority IDs without rendering duplicate entries", () => {
  assert.deepEqual(resolveDepartmentSelection(["2", "2"], directory), {
    resolved: [],
    unresolvedAuthorityIds: ["2"],
    authoritative: false,
  });
});
