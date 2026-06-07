import assert from "node:assert/strict";
import test from "node:test";

import {
  buildRunPlaybackArtifactPreviewRequest,
  openRunPlaybackArtifactPreview,
} from "../runPlaybackArtifactPreview.ts";

test("buildRunPlaybackArtifactPreviewRequest uses download URL when preview URL is missing", () => {
  const preview = buildRunPlaybackArtifactPreviewRequest({
    id: "artifact-1",
    label: "run-output.xlsx",
    type: "office",
    status: "success",
    contentType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    sizeLabel: "12 KB",
    downloadUrl: "/api/ai/artifacts/artifact-1/download",
    previewUrl: null,
    createdAt: null,
  });

  assert.deepEqual(preview, {
    kind: "file",
    previewKey: "artifact:artifact-1",
    filePath: "run-output.xlsx",
    signedUrl: "/api/ai/artifacts/artifact-1/download",
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
});

test("openRunPlaybackArtifactPreview publishes an active manual reveal preview state", () => {
  const states: unknown[] = [];
  const opened = openRunPlaybackArtifactPreview(
    {
      id: "artifact-2",
      label: "run-output.pdf",
      type: "report",
      status: "success",
      contentType: "application/pdf",
      sizeLabel: "1 KB",
      downloadUrl: "/api/ai/artifacts/artifact-2/download",
      previewUrl: null,
      createdAt: null,
    },
    {
      setPreviewState: (next) => states.push(next),
    },
  );

  assert.equal(opened, true);
  assert.deepEqual(states, [
    {
      request: {
        kind: "file",
        previewKey: "artifact:artifact-2",
        filePath: "run-output.pdf",
        signedUrl: "/api/ai/artifacts/artifact-2/download",
        mimeType: "application/pdf",
      },
      source: "manual",
      userInteracted: true,
    },
  ]);
});
