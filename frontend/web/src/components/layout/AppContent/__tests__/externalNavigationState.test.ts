import test from "node:test";
import assert from "node:assert/strict";
import {
  getExternalNavigationTargetFile,
  shouldResetExternalNavigateFlag,
  shouldScrollToBottomAfterExternalNavigation,
} from "../externalNavigationState.ts";

test("resets the external navigation flag only when present", () => {
  assert.equal(
    shouldResetExternalNavigateFlag({ externalNavigate: true }),
    true,
  );
  assert.equal(
    shouldResetExternalNavigateFlag({ externalNavigate: false }),
    false,
  );
  assert.equal(shouldResetExternalNavigateFlag({}), false);
  assert.equal(shouldResetExternalNavigateFlag(null), false);
});

test("marks external navigation requests that should scroll to bottom", () => {
  assert.equal(
    shouldScrollToBottomAfterExternalNavigation({
      externalNavigate: true,
      scrollToBottom: true,
    }),
    true,
  );
  assert.equal(
    shouldScrollToBottomAfterExternalNavigation({
      externalNavigate: true,
      scrollToBottom: false,
    }),
    false,
  );
  assert.equal(
    shouldScrollToBottomAfterExternalNavigation({
      externalNavigate: false,
      scrollToBottom: true,
    }),
    false,
  );
  assert.equal(shouldScrollToBottomAfterExternalNavigation(null), false);
});

test("extracts the target file only for external navigation", () => {
  assert.deepEqual(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {
        fileId: "file-123",
        originalPath: "/tmp/demo.txt",
        traceId: "trace-123",
        source: "reveal_file",
      },
    }),
    {
      fileId: "file-123",
      traceId: "trace-123",
      source: "reveal_file",
    },
  );
  assert.equal(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {},
    }),
    null,
  );
  assert.equal(
    getExternalNavigationTargetFile({
      externalNavigate: false,
      targetFile: {
        fileId: "file-123",
      },
    }),
    null,
  );
  assert.equal(getExternalNavigationTargetFile(null), null);
});

test("drops legacy raw path fields when reading external navigation target file", () => {
  assert.deepEqual(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {
        fileId: "file-legacy-1",
        fileKey: "revealed/.claude/skills/private.pdf",
        fileName: ".claude/skills/private.pdf",
        originalPath: "/workspace/.claude/runs/run-1/private.pdf",
        traceId: "trace-legacy-1",
        source: "reveal_file",
      },
    }),
    {
      fileId: "file-legacy-1",
      traceId: "trace-legacy-1",
      source: "reveal_file",
    },
  );

  assert.equal(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {
        fileKey: "revealed/.claude/skills/private.pdf",
        fileName: "private.pdf",
        originalPath: "/workspace/private.pdf",
      },
    }),
    null,
  );
});
