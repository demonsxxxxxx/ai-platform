import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSubagentPanelState,
  createSubagentPartRenderEntries,
  createSubagentPartRenderKeys,
} from "../SubagentBlocks.tsx";

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

test("subagent parts use the same stable artifact identity rather than list positions", () => {
  const artifact = {
    type: "artifact" as const,
    artifact_id: "artifact-a",
    artifact_type: "document",
    label: "file.txt",
    content_type: "text/plain",
    size_bytes: 1,
    download_url: "/api/ai/artifacts/artifact-a/download",
  };
  const initial = createSubagentPartRenderKeys("agent-a", [artifact]);
  const reconciled = createSubagentPartRenderKeys("agent-a", [
    {
      type: "text" as const,
      content: "status",
    },
    artifact,
  ]);

  assert.equal(initial[0], reconciled[1]);
  assert.match(initial[0], /artifact:artifact-a$/);
});

test("nested subagents render inline once while panel entries retain source indexes", () => {
  const parts = [
    { type: "text" as const, content: "status" },
    {
      type: "subagent" as const,
      agent_id: "nested-agent",
      agent_name: "nested_agent",
      input: "Inspect",
      depth: 1,
    },
    {
      type: "artifact" as const,
      artifact_id: "artifact-a",
      artifact_type: "document",
      label: "file.txt",
      content_type: "text/plain",
      size_bytes: 1,
      download_url: "/api/ai/artifacts/artifact-a/download",
    },
  ];
  const allKeys = createSubagentPartRenderKeys("agent-a", parts);
  const panel = createSubagentPartRenderEntries("agent-a", parts, "panel");
  const nested = createSubagentPartRenderEntries("agent-a", parts, "nested");

  assert.deepEqual(
    panel.map(({ index, key }) => ({ index, key })),
    [
      { index: 0, key: allKeys[0] },
      { index: 2, key: allKeys[2] },
    ],
  );
  assert.deepEqual(
    nested.map(({ index, key }) => ({ index, key })),
    [{ index: 1, key: allKeys[1] }],
  );
  assert.equal(panel.some(({ part }) => part.type === "subagent"), false);
  assert.equal(nested.every(({ part }) => part.type === "subagent"), true);
});
