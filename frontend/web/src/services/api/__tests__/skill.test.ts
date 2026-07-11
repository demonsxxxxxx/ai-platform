import test from "node:test";
import assert from "node:assert/strict";

import {
  buildAdminSkillPreviewUrl,
  buildAdminSkillUploadUrl,
  buildSkillListUrl,
  collectAllAuthorizedSkills,
  normalizeSkillListResponse,
} from "../skill.ts";

test("buildSkillListUrl includes pagination and search params", () => {
  assert.equal(
    buildSkillListUrl({ skip: 20, limit: 10, q: "planner", tags: ["coding"] }),
    "/api/skills/?skip=20&limit=10&q=planner&tags=coding",
  );
});

test("buildAdminSkillUploadUrl targets governed admin package upload", () => {
  assert.equal(
    buildAdminSkillUploadUrl("new research/skill"),
    "/api/ai/admin/skills/new%20research%2Fskill/versions/upload",
  );
});

test("buildAdminSkillPreviewUrl targets global-catalog admin ZIP preview", () => {
  assert.equal(
    buildAdminSkillPreviewUrl(),
    "/api/ai/admin/skills/upload/preview",
  );
});

const userSkill = {
  skill_name: "planner",
  expected_version: "hash-planner-v1",
  input_modes: ["chat"],
  requires_file: false,
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

test("collectAllAuthorizedSkills aggregates beyond 200 and preserves catalog projection", async () => {
  const calls: Array<{ skip?: number; limit?: number }> = [];
  const allSkills = Array.from({ length: 205 }, (_, index) => ({
    ...userSkill,
    skill_name: `skill-${String(index).padStart(3, "0")}`,
    expected_version: `hash-${index}`,
  }));

  const result = await collectAllAuthorizedSkills(async (params) => {
    calls.push(params);
    const skip = params.skip ?? 0;
    const page = allSkills.slice(skip, skip + 200);
    return {
      skills: page,
      total: allSkills.length,
      skip,
      limit: 200,
      available_tags: skip === 0 ? ["planning"] : ["review"],
      effective_permissions: ["skill:read"],
      effective_permissions_known: true,
      catalog_read_resolved: true,
    };
  });

  assert.deepEqual(calls, [
    { skip: 0, limit: 200 },
    { skip: 200, limit: 200 },
  ]);
  assert.equal(result.skills.length, 205);
  assert.equal(result.skills.at(-1)?.skill_name, "skill-204");
  assert.deepEqual(result.available_tags, ["planning", "review"]);
  assert.deepEqual(result.effective_permissions, ["skill:read"]);
  assert.equal(result.effective_permissions_known, true);
  assert.equal(result.catalog_read_resolved, true);
});

test("collectAllAuthorizedSkills refreshes a second-page Skill version without duplicates", async () => {
  let version = "hash-old";
  const listPage = async (params: { skip?: number; limit?: number }) => {
    const skip = params.skip ?? 0;
    const firstPage = Array.from({ length: 200 }, (_, index) => ({
      ...userSkill,
      skill_name: `skill-${index}`,
    }));
    const secondPage = [
      { ...userSkill, skill_name: "target-skill", expected_version: version },
      { ...userSkill, skill_name: "skill-0" },
    ];
    return {
      skills: skip === 0 ? firstPage : skip === 200 ? secondPage : [],
      total: 202,
      skip,
      limit: 200,
      available_tags: ["planning"],
      effective_permissions: ["skill:read"],
      effective_permissions_known: true,
      catalog_read_resolved: true,
    };
  };

  const initial = await collectAllAuthorizedSkills(listPage);
  version = "hash-current";
  const refreshed = await collectAllAuthorizedSkills(listPage);

  assert.equal(initial.skills.length, 201);
  assert.equal(
    initial.skills.find((skill) => skill.skill_name === "target-skill")
      ?.expected_version,
    "hash-old",
  );
  assert.equal(
    refreshed.skills.find((skill) => skill.skill_name === "target-skill")
      ?.expected_version,
    "hash-current",
  );
});

test("collectAllAuthorizedSkills fails closed instead of returning a partial page", async () => {
  await assert.rejects(
    collectAllAuthorizedSkills(async ({ skip = 0 }) => {
      if (skip > 0) throw new Error("page unavailable");
      return {
        skills: Array.from({ length: 200 }, (_, index) => ({
          ...userSkill,
          skill_name: `skill-${index}`,
        })),
        total: 201,
        skip: 0,
        limit: 200,
        available_tags: ["planning"],
        effective_permissions: ["skill:read"],
        effective_permissions_known: true,
        catalog_read_resolved: true,
      };
    }),
    /page unavailable/,
  );
});
