import assert from "node:assert/strict";
import test from "node:test";

import { runFileFallbackDownload } from "../fileFallbackDownload.ts";

test("runFileFallbackDownload prefers onDownload over a raw download URL", () => {
  let onDownloadCalls = 0;
  const createdAnchors: unknown[] = [];

  runFileFallbackDownload({
    onDownload: () => {
      onDownloadCalls += 1;
    },
    downloadUrl: "/api/ai/artifacts/protected/download",
    fileName: "protected.bin",
    documentRef: {
      createElement: () => {
        const anchor = { href: "", download: "", click() {} };
        createdAnchors.push(anchor);
        return anchor;
      },
      body: {
        appendChild() {},
        removeChild() {},
      },
    } as unknown as Document,
  });

  assert.equal(onDownloadCalls, 1);
  assert.deepEqual(createdAnchors, []);
});
