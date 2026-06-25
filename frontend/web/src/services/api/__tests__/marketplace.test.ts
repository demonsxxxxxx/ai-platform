import test from "node:test";
import assert from "node:assert/strict";

import {
  buildMarketplaceListUrl,
  normalizeMarketplaceListResponse,
} from "../marketplace.ts";

test("buildMarketplaceListUrl includes filters and pagination", () => {
  assert.equal(
    buildMarketplaceListUrl({
      tags: "coding,planning",
      search: "doc review",
      skip: 20,
      limit: 10,
    }),
    "/api/marketplace/?tags=coding%2Cplanning&search=doc+review&skip=20&limit=10",
  );
});

test("buildMarketplaceListUrl keeps the public marketplace root stable", () => {
  assert.equal(buildMarketplaceListUrl(), "/api/marketplace/");
});

const marketplaceSkill = {
  skill_name: "planner",
  description: "Planning workflow",
  tags: ["planning"],
  version: "1.0.0",
  is_active: true,
  is_owner: false,
  file_count: 1,
};

test("normalizeMarketplaceListResponse keeps legacy marketplace list arrays compatible", () => {
  assert.deepEqual(normalizeMarketplaceListResponse([marketplaceSkill]), {
    skills: [marketplaceSkill],
    total: 1,
    skip: 0,
    limit: 1,
    available_tags: [],
    effective_permissions: [],
  });
});

test("normalizeMarketplaceListResponse preserves projected marketplace permissions", () => {
  assert.deepEqual(
    normalizeMarketplaceListResponse({
      skills: [marketplaceSkill],
      total: 7,
      skip: 20,
      limit: 10,
      available_tags: ["planning", "review"],
      effective_permissions: ["marketplace:read", "skill:write"],
    }),
    {
      skills: [marketplaceSkill],
      total: 7,
      skip: 20,
      limit: 10,
      available_tags: ["planning", "review"],
      effective_permissions: ["marketplace:read", "skill:write"],
    },
  );
});
