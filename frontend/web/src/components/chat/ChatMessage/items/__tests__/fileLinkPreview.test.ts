import assert from "node:assert/strict";
import test from "node:test";

import { buildFileLinkPreviewRequest } from "../fileLinkPreview.ts";

function encodeRepeated(value: string, times: number): string {
  let encoded = value;
  for (let attempt = 0; attempt < times; attempt += 1) {
    encoded = encodeURIComponent(encoded);
  }
  return encoded;
}

test("buildFileLinkPreviewRequest keeps protected platform file links", () => {
  const preview = buildFileLinkPreviewRequest({
    href: "/api/ai/artifacts/artifact-1/download",
    fileName: "report.pdf",
  });

  assert.deepEqual(preview, {
    kind: "file",
    previewKey: preview?.previewKey,
    filePath: "report.pdf",
    signedUrl: "/api/ai/artifacts/artifact-1/download",
  });
  assert.match(preview?.previewKey ?? "", /^file-link:/);
  assert.doesNotMatch(preview?.previewKey ?? "", /\/api\/ai\/artifacts/);
});

test("buildFileLinkPreviewRequest rejects external http and encoded internal file links", () => {
  assert.equal(
    buildFileLinkPreviewRequest({
      href: "http://cdn.example.com/report.pdf",
      fileName: "report.pdf",
    }),
    null,
  );

  assert.equal(
    buildFileLinkPreviewRequest({
      href: `/api/ai/artifacts/${encodeRepeated(".claude/runs/secret", 4)}/download?filename=secret.pdf`,
      fileName: "secret.pdf",
    }),
    null,
  );
});

test("buildFileLinkPreviewRequest sanitizes unsafe file labels", () => {
  const preview = buildFileLinkPreviewRequest({
    href: "/api/ai/artifacts/artifact-2/download",
    fileName: encodeRepeated(".claude/skills/private.pdf", 4),
  });

  assert.equal(preview?.filePath, "file");
  assert.doesNotMatch(JSON.stringify(preview), /\.claude|%252Eclaude/);
});
