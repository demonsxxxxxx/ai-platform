import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import test from "node:test";
import { RevealPreviewHost } from "../RevealPreviewHost.tsx";
import type { RevealPreviewRequest } from "../revealPreviewData.ts";
import { LazyDocumentPreview } from "../../../../documents/LazyDocumentPreview.tsx";

test("file preview host preserves authorized document props and callbacks", () => {
  const preview: RevealPreviewRequest = {
    kind: "file",
    previewKey: "artifact:report",
    filePath: "report.pdf",
    previewUrl: "/api/ai/artifacts/report/preview",
    downloadUrl: "/api/ai/artifacts/report/download",
    fileSize: 20,
    mimeType: "application/pdf",
  };
  const onClose = () => undefined;
  const onUserInteraction = () => undefined;
  const element = RevealPreviewHost({ preview, onClose, onUserInteraction });
  assert.equal(element?.type, LazyDocumentPreview);
  assert.equal(element?.key, preview.previewKey);
  assert.equal(element?.props.path, preview.filePath);
  assert.equal(element?.props.previewUrl, preview.previewUrl);
  assert.equal(element?.props.downloadUrl, preview.downloadUrl);
  assert.equal(element?.props.fileSize, preview.fileSize);
  assert.equal(element?.props.mimeType, preview.mimeType);
  assert.equal(element?.props.onClose, onClose);
  assert.equal(element?.props.onUserInteraction, onUserInteraction);
});

test("empty or legacy project preview payload cannot mount an execution preview", () => {
  assert.equal(RevealPreviewHost({ preview: null, onClose() {} }), null);
  const stale = {
    kind: "project",
    previewKey: "legacy",
    project: { files: { "secret.txt": "synthetic" } },
  };
  assert.equal(
    RevealPreviewHost({
      preview: stale as unknown as RevealPreviewRequest,
      onClose() {},
    }),
    null,
  );
});

test("retired project execution adapters and direct dependencies stay absent", () => {
  const root = new URL("../../../../../../", import.meta.url);
  const pkg = JSON.parse(readFileSync(new URL("package.json", root), "utf8"));
  for (const dependency of [
    "@codesandbox/sandpack-react",
    "@codesandbox/sandpack-themes",
    "@stackblitz/sdk",
  ]) {
    assert.equal(pkg.dependencies[dependency], undefined);
  }
  for (const file of [
    "ProjectPreview.tsx",
    "LazyProjectPreview.tsx",
    "StackBlitzPreview.tsx",
  ]) {
    assert.equal(existsSync(new URL(`src/components/documents/previews/${file}`, root)), false);
  }
  assert.equal(existsSync(new URL("src/utils/exportProjectZip.ts", root)), true);
});
