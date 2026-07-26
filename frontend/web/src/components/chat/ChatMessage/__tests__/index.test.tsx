import assert from "node:assert/strict";
import test from "node:test";
import type { ArtifactPart, MessagePart, RunStatusPart } from "../../../../types";
import { createMessagePartRenderKeys } from "../MessagePartRenderer.tsx";

const artifactA: ArtifactPart = {
  type: "artifact",
  artifact_id: "artifact-a",
  artifact_type: "document",
  label: "first.docx",
  content_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  size_bytes: 12,
  download_url: "/api/ai/artifacts/artifact-a/download",
};

const artifactB: ArtifactPart = {
  ...artifactA,
  artifact_id: "artifact-b",
  label: "second.docx",
  download_url: "/api/ai/artifacts/artifact-b/download",
};

const transientStatus: RunStatusPart = {
  type: "run_status",
  event_id: "status-1",
  event_type: "queued",
  stage: "download",
  message: "queued",
  severity: "info",
};

test("artifact render identity survives visibility reconciliation and isolates replacement artifacts", () => {
  const initialKeys = createMessagePartRenderKeys("message-1", [artifactA]);
  const artifactAKey = initialKeys[0];
  const afterInsertKeys = createMessagePartRenderKeys("message-1", [
    transientStatus,
    artifactA,
  ]);
  assert.equal(afterInsertKeys[1], "message-1:artifact:artifact-a");

  const replacementKeys = createMessagePartRenderKeys("message-1", [artifactB]);
  const artifactBKey = replacementKeys[0];
  assert.notEqual(artifactBKey, artifactAKey);

  const reorderedKeys = createMessagePartRenderKeys("message-1", [
    artifactA,
    transientStatus,
    artifactB,
  ] as MessagePart[]);
  assert.equal(reorderedKeys[0], "message-1:artifact:artifact-a");
  assert.equal(reorderedKeys[2], "message-1:artifact:artifact-b");
});
