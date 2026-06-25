import test from "node:test";
import assert from "node:assert/strict";

import { buildSkillListUrl, normalizeSkillListResponse } from "../skill.ts";

test("buildSkillListUrl includes pagination and search params", () => {
  assert.equal(
    buildSkillListUrl({ skip: 20, limit: 10, q: "planner", tags: ["coding"] }),
    "/api/skills/?skip=20&limit=10&q=planner&tags=coding",
  );
});

const userSkill = {
  skill_name: "planner",
  description: "Planning workflow",
  tags: ["planning"],
  files: ["SKILL.md"],
  enabled: true,
  file_count: 1,
  installed_from: "marketplace" as const,
  is_published: true,
  marketplace_is_active: true,
};

test("normalizeSkillListResponse preserves projected PR177 skill permissions", () => {
  assert.deepEqual(
    normalizeSkillListResponse({
      skills: [userSkill],
      total: 7,
      skip: 20,
      limit: 10,
      available_tags: ["planning", "review"],
      effective_permissions: ["skill:read", "marketplace:read"],
    }),
    {
      skills: [userSkill],
      total: 7,
      skip: 20,
      limit: 10,
      available_tags: ["planning", "review"],
      effective_permissions: ["skill:read", "marketplace:read"],
      effective_permissions_known: true,
      catalog_read_resolved: true,
    },
  );
});

test("normalizeSkillListResponse keeps legacy arrays readable but permission-unknown", () => {
  assert.deepEqual(normalizeSkillListResponse([userSkill]), {
    skills: [userSkill],
    total: 1,
    skip: 0,
    limit: 1,
    available_tags: [],
    effective_permissions: [],
    effective_permissions_known: false,
    catalog_read_resolved: true,
  });
});
