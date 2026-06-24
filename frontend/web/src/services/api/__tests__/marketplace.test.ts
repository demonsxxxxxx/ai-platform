import test from "node:test";
import assert from "node:assert/strict";

import { buildMarketplaceListUrl } from "../marketplace.ts";

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
