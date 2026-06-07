import assert from "node:assert/strict";
import test from "node:test";

import { buildArtifactPreviewRequest } from "../artifactPreview.ts";

test("builds a file preview request from download_url when preview_url is null", () => {
  const preview = buildArtifactPreviewRequest({
    artifact_id: "artifact-1",
    artifact_type: "office",
    label: "review.docx",
    content_type:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    size_bytes: 128,
    download_url: "/api/ai/artifacts/artifact-1/download",
    preview_url: null,
  });

  assert.deepEqual(preview, {
    kind: "file",
    previewKey: "artifact:artifact-1",
    filePath: "review.docx",
    signedUrl: "/api/ai/artifacts/artifact-1/download",
    fileSize: 128,
    mimeType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
});

test("keeps private artifact fields out of preview requests", () => {
  const preview = buildArtifactPreviewRequest({
    artifact_id: "artifact-2",
    label: "report",
    content_type: "application/pdf",
    size_bytes: 256,
    download_url: "/api/ai/artifacts/artifact-2/download",
    preview_url: null,
    storage_key: "tenant/private/report.pdf",
    work_dir: "/workspace/.claude/runs/run-1",
    manifest: { storage_key: "tenant/private/manifest.json" },
    command_sha256: "sha256:abc",
    resource_limits: { cpu: 2 },
  });

  assert.equal(preview?.filePath, "report.pdf");
  assert.equal(preview?.previewKey, "artifact:artifact-2");
  assert.equal(preview?.signedUrl, "/api/ai/artifacts/artifact-2/download");
  const serializedPreview = JSON.stringify(preview);
  assert.doesNotMatch(serializedPreview, /storage_key|work_dir|manifest/);
  assert.doesNotMatch(serializedPreview, /\.claude|command_sha256/);
  assert.doesNotMatch(serializedPreview, /resource_limits|tenant\/private/);
});

test("rejects unsafe artifact preview URLs before they enter preview state", () => {
  const encodedRunsPath = encodeURIComponent(
    encodeURIComponent(".claude/runs/run-1/private.pdf"),
  );

  const externalHttpPreview = buildArtifactPreviewRequest({
    artifact_id: "artifact-http",
    label: "unsafe.pdf",
    content_type: "application/pdf",
    download_url: null,
    preview_url: "http://cdn.example.com/unsafe.pdf",
  });
  assert.equal(externalHttpPreview, null);

  const encodedInternalPreview = buildArtifactPreviewRequest({
    artifact_id: "artifact-internal",
    label: "internal.pdf",
    content_type: "application/pdf",
    download_url: `https://cdn.example.com/${encodedRunsPath}`,
    preview_url: null,
  });
  assert.equal(encodedInternalPreview, null);
});

test("falls back from an unsafe artifact preview URL to a safe download URL", () => {
  const preview = buildArtifactPreviewRequest({
    artifact_id: "artifact-safe-fallback",
    label: "fallback.pdf",
    content_type: "application/pdf",
    download_url: "/api/ai/artifacts/artifact-safe-fallback/download",
    preview_url: "http://cdn.example.com/unsafe.pdf",
  });

  assert.equal(
    preview?.signedUrl,
    "/api/ai/artifacts/artifact-safe-fallback/download",
  );
});

test("returns null when an artifact has no previewable or downloadable URL", () => {
  const preview = buildArtifactPreviewRequest({
    artifact_id: "artifact-3",
    label: "empty.txt",
    content_type: "text/plain",
    size_bytes: 0,
    preview_url: null,
  });

  assert.equal(preview, null);
});
