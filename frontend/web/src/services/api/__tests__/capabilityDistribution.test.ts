import test from "node:test";
import assert from "node:assert/strict";

import {
  buildCapabilityDistributionListUrl,
  buildCapabilityDistributionUrl,
} from "../capabilityDistribution.ts";

test("buildCapabilityDistributionListUrl targets the governed admin distribution route", () => {
  assert.equal(
    buildCapabilityDistributionListUrl({
      capabilityKind: "skill",
      includeDisabled: true,
    }),
    "/api/admin/capability-distributions?capability_kind=skill&include_disabled=true",
  );
});

test("buildCapabilityDistributionUrl keeps capability-kind and id encoded under admin route", () => {
  assert.equal(
    buildCapabilityDistributionUrl("mcp_server", "qa/review server"),
    "/api/admin/capability-distributions/mcp_server/qa%2Freview%20server",
  );
});
