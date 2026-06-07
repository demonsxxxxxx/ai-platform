import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("reveal artifacts summary mirrors the file tree view row details", () => {
  const summarySource = readFileSync(
    new URL("../RevealArtifactsSummary.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    summarySource,
    /getRevealArtifactSafeImageSrc/,
    "file rows should route thumbnails through the safe image source selector",
  );
  assert.match(
    summarySource,
    /<img[\s\S]*src=\{imageSrc\}/,
    "image file rows should render a thumbnail only from the safe preview source",
  );
  assert.match(
    summarySource,
    /formatSize\(dirSize\)/,
    "directory rows should show the aggregated size like FileTreeView",
  );
});

test("all files summary does not download, copy, or image-preview raw protected artifact URLs", () => {
  const summarySource = readFileSync(
    new URL("../RevealArtifactsSummary.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    summarySource,
    /downloadArtifactFile/,
    "summary downloads should use the unified artifact download helper",
  );
  assert.match(
    summarySource,
    /shouldUseAuthenticatedDocumentRequest/,
    "summary image sources should detect protected platform URLs",
  );
  assert.match(
    summarySource,
    /isAllowedRevealArtifactUrl/,
    "summary image sources should use the reveal artifact URL allowlist",
  );
  assert.match(
    summarySource,
    /!isAllowedRevealArtifactUrl\(rawImageSrc\)/,
    "raw image thumbnails should be rejected before they reach an img src",
  );
  assert.doesNotMatch(
    summarySource,
    /downloadFile\([^)]*node\.artifact\.preview\.signedUrl/s,
    "summary downloads must not click raw signedUrl anchors",
  );
  assert.doesNotMatch(
    summarySource,
    /copyToClipboard\(\s*node\.artifact\.preview\.signedUrl/s,
    "copy should not expose raw artifact URLs",
  );
  assert.doesNotMatch(
    summarySource,
    /const imageSrc = isImageFile\(ext\) \? node\.artifact\.preview\.signedUrl : null/,
    "image thumbnails must not blindly use signedUrl",
  );
  assert.doesNotMatch(
    summarySource,
    /artifact\.preview\.imageUrl \|\| artifact\.preview\.signedUrl/,
    "ImageViewer items must not blindly use signedUrl as their src",
  );
});

test("all files image rows open an ImageViewer gallery with navigation", () => {
  const summarySource = readFileSync(
    new URL("../RevealArtifactsSummary.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    summarySource,
    /import\s+\{[^}]*ImageViewer[^}]*\}\s+from\s+"..\/..\/common"/,
    "all files panel should use the shared fullscreen image viewer",
  );
  assert.match(
    summarySource,
    /getRevealArtifactImagePreviewItems/,
    "all files panel should derive image gallery items from reveal artifacts",
  );
  assert.match(
    summarySource,
    /onOpenImagePreview=/,
    "image file rows should open the local image gallery",
  );
  assert.match(
    summarySource,
    /<ImageViewer[\s\S]*?\bonPrevious=/,
    "gallery should wire previous navigation",
  );
  assert.match(
    summarySource,
    /<ImageViewer[\s\S]*?\bonNext=/,
    "gallery should wire next navigation",
  );
  assert.match(
    summarySource,
    /<ImageViewer[\s\S]*?\bpositionLabel=/,
    "gallery should show the image position",
  );
});
