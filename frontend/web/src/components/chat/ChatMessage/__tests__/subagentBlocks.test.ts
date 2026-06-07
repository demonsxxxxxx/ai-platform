import assert from "node:assert/strict";
import test from "node:test";
import { buildSubagentPanelState } from "../SubagentBlocks.tsx";

test("subagent panel subtitle shows only the start time", () => {
  const startedAt = Date.UTC(2026, 4, 10, 1, 45, 54);
  const completedAt = startedAt + 26_076 * 60_000 + 2_000;

  const state = buildSubagentPanelState({
    agentId: "agent-a",
    agentName: "worker_agent",
    input: "Do work",
    status: "complete",
    startedAt,
    completedAt,
  });

  assert.equal(
    state.subtitle,
    new Date(startedAt).toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }),
  );
  assert.ok(!state.subtitle?.includes(" · "));
  assert.ok(!state.subtitle?.includes("26076m 2s"));
});
