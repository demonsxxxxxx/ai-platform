import test from "node:test";
import assert from "node:assert/strict";
import { resolveAvailableAgentId } from "../agentSelection";

const agents = [
  { id: "search", name: "Search", description: "", version: "1.0.0" },
  { id: "fast", name: "Fast", description: "", version: "1.0.0" },
];

test("falls back to the first available agent when the default agent is unavailable", () => {
  assert.equal(resolveAvailableAgentId("", "default", agents), "search");
});

test("keeps the current agent when it is still available", () => {
  assert.equal(resolveAvailableAgentId("fast", "search", agents), "fast");
});

test("replaces an unavailable current agent with the first available agent", () => {
  assert.equal(resolveAvailableAgentId("default", "default", agents), "search");
});
