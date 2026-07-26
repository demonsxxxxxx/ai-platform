import assert from "node:assert/strict";
import test from "node:test";
import {
  createSubagentPanelStore,
  type SubagentPanelData,
} from "../subagentPanelStore.ts";
import {
  createArtifactDownloadScope,
  createArtifactDownloadScopeContext,
} from "../items/artifactDownloadRegistry.ts";

function createData(agentId: string): SubagentPanelData {
  return {
    agentId,
    agentName: `agent-${agentId}`,
    input: `input-${agentId}`,
    status: "running",
  };
}

test("notifies only listeners subscribed to the updated agent id", () => {
  const store = createSubagentPanelStore();
  const calls: string[] = [];

  store.subscribe("agent-a", () => calls.push("a"));
  store.subscribe("agent-b", () => calls.push("b"));

  store.set(createData("agent-a"));

  assert.deepEqual(calls, ["a"]);
});

test("notifies listeners when an agent entry is deleted", () => {
  const store = createSubagentPanelStore();
  const calls: string[] = [];

  store.set(createData("agent-a"));
  store.subscribe("agent-a", () => calls.push("a"));

  store.delete("agent-a");

  assert.deepEqual(calls, ["a"]);
  assert.equal(store.get("agent-a"), undefined);
});

test("tracks current store size for lightweight observability", () => {
  const store = createSubagentPanelStore();

  store.set(createData("agent-a"));
  store.set(createData("agent-b"));
  store.delete("agent-a");

  assert.equal(store.size(), 1);
});

test("keeps the authenticated artifact scope with the subagent panel across a panel remount", () => {
  const store = createSubagentPanelStore();
  const context = createArtifactDownloadScopeContext({
    tenantId: "tenant-a",
    userId: "user-a",
    roles: ["member"],
    isActive: true,
    sessionId: "session-a",
  })!;
  const scope = createArtifactDownloadScope(context, "message-a")!;
  const updates: SubagentPanelData[] = [];
  const unsubscribe = store.subscribe("agent-a", () => {
    const current = store.get("agent-a");
    if (current) updates.push(current);
  });

  store.set({
    ...createData("agent-a"),
    artifactDownloadScope: scope,
    parts: [
      {
        type: "artifact",
        artifact_id: "artifact-a",
        artifact_type: "document",
        label: "artifact.txt",
        content_type: "text/plain",
        size_bytes: 1,
        download_url: "/api/ai/artifacts/artifact-a/download",
      },
    ],
  });

  const remountedPanel = store.get("agent-a");
  assert.equal(remountedPanel?.artifactDownloadScope?.key, scope.key);
  assert.equal(remountedPanel?.artifactDownloadScope?.sessionId, "session-a");
  assert.equal(updates.length, 1);
  unsubscribe();
});
