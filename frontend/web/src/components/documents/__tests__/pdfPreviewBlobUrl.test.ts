import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const stateSource = readFileSync(
  new URL("../useDocumentPreviewState.ts", import.meta.url),
  "utf8",
);
const contentSource = readFileSync(
  new URL("../DocumentPreviewContent.tsx", import.meta.url),
  "utf8",
);

function sliceBetween(source: string, start: string, end: string): string {
  const startIndex = source.indexOf(start);
  assert.notEqual(startIndex, -1, `${start} branch should exist`);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(endIndex, -1, `${end} branch should follow ${start}`);
  return source.slice(startIndex, endIndex);
}

test("PDF preview uses a local PDF blob URL instead of embedding the download URL directly", () => {
  const pdfBranch = sliceBetween(
    stateSource,
    "if (resolvedPdfFile) {",
    "if (resolvedVideoFile) {",
  );
  assert.match(pdfBranch, /fetchDocumentArrayBuffer\(url\)/);
  assert.match(
    pdfBranch,
    /new Blob\(\[.*\], \{ type: "application\/pdf" \}\)/s,
  );
  assert.match(pdfBranch, /URL\.createObjectURL/);
  assert.doesNotMatch(pdfBranch, /setPdfUrl\(url\)/);
});

test("PDF preview revokes generated blob URLs", () => {
  assert.match(stateSource, /if \(pdfUrl\?\.startsWith\("blob:"\)\)/);
  assert.match(stateSource, /URL\.revokeObjectURL\(pdfUrl\)/);
});

test("remote document preview validates URLs before storing or fetching them", () => {
  const remoteBranch = sliceBetween(
    stateSource,
    "if (s3Key || signedUrl) {",
    "setError(t(\"documents.noContent\"",
  );
  const guardIndex = remoteBranch.indexOf("assertSafeDocumentPreviewUrl(url);");
  const setResolvedIndex = remoteBranch.indexOf("setResolvedUrl(url);");
  const arrayFetchIndex = remoteBranch.indexOf("fetchDocumentArrayBuffer(url)");
  const textFetchIndex = remoteBranch.indexOf("fetchDocumentText(url)");

  assert.notEqual(guardIndex, -1, "remote URL guard should exist");
  assert.ok(
    guardIndex < setResolvedIndex,
    "remote URL guard should run before resolvedUrl enters preview state",
  );
  assert.ok(
    guardIndex < arrayFetchIndex,
    "remote URL guard should run before array-buffer fetches",
  );
  assert.ok(
    guardIndex < textFetchIndex,
    "remote URL guard should run before text fetches",
  );
});

test("external image preview validates URLs before storing image src", () => {
  const externalImageBranch = sliceBetween(
    stateSource,
    "if (externalImageUrl) {",
    "if (content !== undefined) {",
  );
  const guardIndex = externalImageBranch.indexOf(
    "assertSafeDocumentPreviewUrl(externalImageUrl);",
  );
  const setImageIndex = externalImageBranch.indexOf(
    "setImageUrl(externalImageUrl);",
  );

  assert.notEqual(guardIndex, -1, "external image URL guard should exist");
  assert.ok(
    guardIndex < setImageIndex,
    "external image URL guard should run before image src enters preview state",
  );
});

test("unsupported preview files render a guardrail instead of auto-downloading", () => {
  const unsupportedBranch = sliceBetween(
    contentSource,
    "if (unsupportedPreviewFile) {",
    "if (resolvedPdfFile) {",
  );
  assert.doesNotMatch(unsupportedBranch, /document\.createElement\("a"\)/);
  assert.match(contentSource, /documents\.unsupportedFilePreview/);
  assert.match(contentSource, /documents\.unsupportedFileHint/);
});

test("binary and unsupported fallback panels use the unified download handler", () => {
  const binaryBranch = sliceBetween(
    contentSource,
    "resolvedBinaryFile &&",
    "if (unsupportedPreviewFile) {",
  );
  const unsupportedBranch = sliceBetween(
    contentSource,
    "if (unsupportedPreviewFile) {",
    "if (resolvedPdfFile) {",
  );
  const legacyDocBranch = sliceBetween(
    contentSource,
    "if (legacyDocFile && docUrl) {",
    "if (wordPreviewFile && arrayBuffer) {",
  );

  for (const branch of [binaryBranch, unsupportedBranch, legacyDocBranch]) {
    assert.match(branch, /onDownload=\{handleDownload\}/);
    assert.doesNotMatch(
      branch,
      /downloadUrl=\{(?:resolvedUrl \|\| signedUrl|docUrl)\}/,
    );
  }
});

test("protected image previews are loaded through the document fetch cache before rendering", () => {
  const imageBranch = sliceBetween(
    stateSource,
    "if (resolvedImageFile) {",
    "if (resolvedPdfFile) {",
  );
  assert.match(imageBranch, /resolveDocumentPreviewUrl/);
});

test("PowerPoint previews use local array buffers instead of bare URLs", () => {
  const pptBranch = sliceBetween(
    stateSource,
    "if (pptFile) {",
    "if (htmlFile) {",
  );
  assert.match(pptBranch, /resolvePptPreviewBuffer/);
  assert.match(pptBranch, /setPptxBuffer\(buffer\)/);
});
