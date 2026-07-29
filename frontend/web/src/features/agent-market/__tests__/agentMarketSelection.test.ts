import assert from "node:assert/strict";
import test from "node:test";

import {
  buildAgentMarketDetailPath,
  filterPublishedMarketProfiles,
  selectPublishedMarketProfile,
} from "../agentMarketSelection";

const profile = {
  agent_id: "agt_support",
  expected_revision: 4,
  name: "支持助手",
  description: "已发布的支持服务。",
};

test("market accepts only the exact published profile revision from its route", () => {
  assert.equal(selectPublishedMarketProfile([profile], "agt_support", "4"), profile);
  assert.equal(selectPublishedMarketProfile([profile], "agt_support", "5"), null);
  assert.equal(selectPublishedMarketProfile([profile], "agt_support", "not-a-revision"), null);
});

test("market detail uses only the exact published profile identity", () => {
  assert.equal(
    buildAgentMarketDetailPath(profile),
    "/agent-market/agt_support/4",
  );
});

test("market search uses only current safe public name and description fields", () => {
  const profiles = [
    profile,
    {
      agent_id: "agt_finance",
      expected_revision: 2,
      name: "财务助手",
      description: "核对报销材料",
    },
  ];

  assert.deepEqual(filterPublishedMarketProfiles(profiles, " 支持 "), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "报销"), [profiles[1]]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, ""), profiles);
});
