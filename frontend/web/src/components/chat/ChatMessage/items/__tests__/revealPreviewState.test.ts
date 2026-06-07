import test from "node:test";
import assert from "node:assert/strict";
import {
  createActiveRevealPreviewState,
  shouldAcceptRevealPreviewOpen,
  shouldStabilizeScrollForAutoPreviewOpen,
} from "../revealPreviewState.ts";

test("marks external previews as already interacted", () => {
  const previewState = createActiveRevealPreviewState(
    {
      kind: "file",
      previewKey: "external-file:file-1",
      filePath: "/tmp/demo.txt",
    },
    "external",
  );

  assert.equal(previewState.source, "external");
  assert.equal(previewState.userInteracted, true);
});

test("blocks auto preview from replacing an external navigation preview", () => {
  const activePreview = createActiveRevealPreviewState(
    {
      kind: "file",
      previewKey: "external-file:file-1",
      filePath: "/tmp/demo.txt",
    },
    "external",
  );

  assert.equal(
    shouldAcceptRevealPreviewOpen({
      activePreview,
      nextPreview: {
        kind: "file",
        previewKey: "session-file:file-2",
        filePath: "/tmp/other.txt",
      },
      source: "auto",
      dismissedPreviewKeys: new Set<string>(),
    }),
    false,
  );
});

test("still allows manual preview to replace an external navigation preview", () => {
  const activePreview = createActiveRevealPreviewState(
    {
      kind: "file",
      previewKey: "external-file:file-1",
      filePath: "/tmp/demo.txt",
    },
    "external",
  );

  assert.equal(
    shouldAcceptRevealPreviewOpen({
      activePreview,
      nextPreview: {
        kind: "file",
        previewKey: "manual-file:file-2",
        filePath: "/tmp/other.txt",
      },
      source: "manual",
      dismissedPreviewKeys: new Set<string>(),
    }),
    true,
  );
});

test("stabilizes chat scroll only when an auto preview opens near the bottom", () => {
  const autoPreview = createActiveRevealPreviewState(
    {
      kind: "project",
      previewKey: "project:/tmp/demo",
      project: {
        version: 1,
        name: "demo",
        path: "/tmp/demo",
        template: "static",
        mode: "folder",
        files: {},
        fileCount: 0,
      },
    },
    "auto",
  );

  assert.equal(
    shouldStabilizeScrollForAutoPreviewOpen({
      previousPreview: null,
      nextPreview: autoPreview,
      isNearBottom: true,
    }),
    true,
  );
  assert.equal(
    shouldStabilizeScrollForAutoPreviewOpen({
      previousPreview: null,
      nextPreview: autoPreview,
      isNearBottom: false,
    }),
    false,
  );
  assert.equal(
    shouldStabilizeScrollForAutoPreviewOpen({
      previousPreview: autoPreview,
      nextPreview: autoPreview,
      isNearBottom: true,
    }),
    false,
  );
});
