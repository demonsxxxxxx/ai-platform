import test from "node:test";
import assert from "node:assert/strict";

import { buildRoleListUrl } from "../role.ts";

test("buildRoleListUrl includes pagination and search params", () => {
  assert.equal(
    buildRoleListUrl({ skip: 20, limit: 10, q: "admin" }),
    "/api/roles/?skip=20&limit=10&q=admin",
  );
});
