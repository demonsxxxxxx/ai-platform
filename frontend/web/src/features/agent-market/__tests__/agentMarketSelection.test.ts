import assert from "node:assert/strict";
import test from "node:test";

import type { AgentProfilePublicProjection } from "../../../types";

import {
  buildAgentMarketDetailPath,
  buildAgentMarketWorkspacePath,
  filterPublishedMarketProfiles,
  filterPublishedMarketProfilesByTags,
  selectPublishedMarketProfile,
} from "../agentMarketSelection";

const profile: AgentProfilePublicProjection = {
  agent_id: "agt_support",
  expected_revision: 4,
  name: "支持助手",
  description: "在授权范围内处理企业支持请求。",
  starter_prompts: ["帮我处理支持请求", "支持请求分流"],
  avatar_ref: "builtin:assistant" as const,
  avatar_seed: "agt-support",
  market_tags: ["客户支持", "写作"],
  is_favorite: false,
  published_at: "2026-08-04T01:00:00Z",
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

test("market workspace deep links preserve the immutable published revision", () => {
  assert.equal(
    buildAgentMarketWorkspacePath(profile),
    "/agent-market/agt_support/4/chat",
  );
  assert.equal(
    buildAgentMarketWorkspacePath(profile, "session/42"),
    "/agent-market/agt_support/4/chat/session%2F42",
  );
});

test("market tag filters select the union of all clicked tags", () => {
  const profiles = [
    profile,
    {
      ...profile,
      agent_id: "agt_finance",
      expected_revision: 2,
      name: "财务助手",
      market_tags: ["财务"],
    },
    {
      ...profile,
      agent_id: "agt_hr",
      expected_revision: 3,
      name: "人事助手",
      market_tags: ["人力资源"],
    },
  ];

  assert.deepEqual(
    filterPublishedMarketProfilesByTags(profiles, ["客户支持", "财务"]),
    [profile, profiles[1]],
  );
  assert.deepEqual(filterPublishedMarketProfilesByTags(profiles, []), profiles);
});

test("market search covers the safe public identity and use fields", () => {
  const profiles = [
    profile,
    {
      ...profile,
      agent_id: "agt_finance",
      expected_revision: 2,
      name: "财务助手",
      description: "核对企业财务单据和报销材料。",
      starter_prompts: ["报销材料核验"],
      market_tags: [],
      avatar_ref: "builtin:document" as const,
    },
  ];

  assert.deepEqual(filterPublishedMarketProfiles(profiles, " 支持 "), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "报销"), [profiles[1]]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "授权范围"), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "支持请求分流"), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "客户支持"), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "写作"), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, ""), profiles);
});
