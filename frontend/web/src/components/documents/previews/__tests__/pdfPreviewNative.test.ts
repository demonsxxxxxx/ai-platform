import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../PdfPreview.tsx", import.meta.url),
  "utf8",
);
const frameSource = readFileSync(
  new URL("../DocumentViewerFrame.tsx", import.meta.url),
  "utf8",
);
const frontendPackage = JSON.parse(
  readFileSync(new URL("../../../../../package.json", import.meta.url), "utf8"),
);

test("PDF preview renders through PDF.js instead of a native embedded viewer", () => {
  assert.match(source, /from\s+"react-pdf"/);
  assert.match(source, /\bDocument\b/);
  assert.match(source, /\bPage\b/);
  assert.doesNotMatch(source, /<iframe\b/);
});

test("PDF preview renders all pages in a continuous scroll surface", () => {
  assert.match(source, /numPages/);
  assert.match(source, /Array\.from\(\{\s*length:\s*numPages\s*\}/);
  assert.match(source, /pageNumber=\{pageNumber \+ 1\}/);
  assert.match(frameSource, /overflow-auto/);
});

test("PDF preview uses shared zoom controls without page navigation controls", () => {
  assert.match(source, /<DocumentViewerFrame/);
  assert.match(frameSource, /<ViewerToolbar/);
  assert.match(frameSource, /zoomIn/);
  assert.match(frameSource, /zoomOut/);
  assert.doesNotMatch(source, /goToPrevPage|goToNextPage/);
  assert.doesNotMatch(source, /ChevronLeft|ChevronRight/);
  assert.doesNotMatch(source, /previousPage|nextPage/);
});

test("shared document previews preserve PDF mobile gestures", () => {
  assert.match(frameSource, /pinchStartRef/);
  assert.match(frameSource, /handleTouchStart/);
  assert.match(frameSource, /handleTouchMove/);
  assert.match(frameSource, /handleTouchEnd/);
  assert.match(frameSource, /toggleDoubleTapZoom/);
  assert.match(frameSource, /touchAction:\s*"none"/);
  assert.match(frameSource, /scrollLeft/);
  assert.match(frameSource, /scrollTop/);
});

test("PDF preview keeps a user-facing fallback when rendering fails", () => {
  assert.match(source, /loadFailed/);
  assert.match(source, /documents\.pdfPreviewUnavailable/);
  assert.match(source, /documents\.openInNewTab/);
});

test("PDF preview uses a PDF.js worker version compatible with react-pdf", () => {
  assert.equal(frontendPackage.dependencies["react-pdf"], "^10.4.0");
  assert.equal(frontendPackage.dependencies["pdfjs-dist"], "^5.4.296");
});
