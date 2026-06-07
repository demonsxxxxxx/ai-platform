import test from "node:test";
import assert from "node:assert/strict";

import { buildSkillListUrl } from "../skill.ts";

test("buildSkillListUrl includes pagination and search params", () => {
  assert.equal(
    buildSkillListUrl({ skip: 20, limit: 10, q: "planner", tags: ["coding"] }),
    "/api/skills/?skip=20&limit=10&q=planner&tags=coding",
  );
});
