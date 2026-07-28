import assert from "node:assert/strict";
import test from "node:test";

import {
  consumePendingAgentMarketSelection,
  marketProfileRequest,
  selectPublishedMarketProfile,
  setPendingAgentMarketSelection,
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

test("market forwards only the immutable profile lock to Chat", () => {
  assert.deepEqual(marketProfileRequest(profile), {
    agent_id: "agt_support",
    expected_revision: 4,
  });
});

test("market selection is one-shot and never persisted in a URL or local storage", () => {
  setPendingAgentMarketSelection(marketProfileRequest(profile));
  assert.deepEqual(consumePendingAgentMarketSelection(), {
    agent_id: "agt_support",
    expected_revision: 4,
  });
  assert.equal(consumePendingAgentMarketSelection(), null);
});
