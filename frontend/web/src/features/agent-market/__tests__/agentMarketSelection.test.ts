import assert from "node:assert/strict";
import test from "node:test";

import type { AgentProfilePublicProjection } from "../../../types";

import {
  buildAgentMarketDetailPath,
  buildAgentMarketWorkspacePath,
  filterPublishedMarketProfiles,
  selectPublishedMarketProfile,
} from "../agentMarketSelection";

const profile: AgentProfilePublicProjection = {
  agent_id: "agt_support",
  expected_revision: 4,
  name: "支持助手",
  description: "已发布的支持服务。",
  welcome_message: "欢迎使用支持助手。",
  starter_prompts: ["帮我处理支持请求"],
  capability_summary: "在授权范围内处理企业支持请求。",
  recommended_tasks: ["支持请求分流"],
  supported_input_types: ["text"],
  expected_outputs: ["处理建议"],
  permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
  avatar_ref: "builtin:assistant" as const,
  category: "support" as const,
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

test("market search covers the safe public identity and use fields", () => {
  const profiles = [
    profile,
    {
      ...profile,
      agent_id: "agt_finance",
      expected_revision: 2,
      name: "财务助手",
      description: "核对报销材料",
      capability_summary: "核对企业财务单据。",
      recommended_tasks: ["报销材料核验"],
      avatar_ref: "builtin:document" as const,
      category: "operations" as const,
    },
  ];

  assert.deepEqual(filterPublishedMarketProfiles(profiles, " 支持 "), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "报销"), [profiles[1]]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "授权范围"), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, "支持请求分流"), [profile]);
  assert.deepEqual(filterPublishedMarketProfiles(profiles, ""), profiles);
});
