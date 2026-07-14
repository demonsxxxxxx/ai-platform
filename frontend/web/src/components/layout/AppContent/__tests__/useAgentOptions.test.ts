import test from "node:test";
import assert from "node:assert/strict";
import { CHAT_AGENT_OPTION_DEFINITIONS } from "../../../../types/agentOptions.ts";
import { getAgentOptionSyncMode, normalizeAgentOptions } from "../useAgentOptions";

test("keeps the real thinking parameter without an agent directory", () => {
  const options = normalizeAgentOptions(CHAT_AGENT_OPTION_DEFINITIONS);

  assert.equal(options?.enable_thinking.default, "off");
  assert.deepEqual(
    options?.enable_thinking.options?.map((item) => item.value),
    ["off", "low", "medium", "high", "max"],
  );
});

test("resets option values when definitions initialize", () => {
  assert.equal(
    getAgentOptionSyncMode({
      optionsJson: '{"enable_thinking":{"default":"medium"}}',
      previousOptionsJson: "",
      hasPendingRestoredOptions: false,
    }),
    "reset",
  );
});

test("applies restored session options before skip checks", () => {
  assert.equal(
    getAgentOptionSyncMode({
      optionsJson: '{"enable_thinking":{"default":"medium"}}',
      previousOptionsJson: '{"enable_thinking":{"default":"medium"}}',
      hasPendingRestoredOptions: true,
    }),
    "restore",
  );
});

test("preserves overlapping values when option definitions change", () => {
  assert.equal(
    getAgentOptionSyncMode({
      optionsJson: '{"enable_thinking":{"default":"high"}}',
      previousOptionsJson: '{"enable_thinking":{"default":"medium"}}',
      hasPendingRestoredOptions: false,
    }),
    "preserve",
  );
});
