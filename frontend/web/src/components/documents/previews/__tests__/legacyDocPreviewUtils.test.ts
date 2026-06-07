import assert from "node:assert/strict";
import test from "node:test";

import { isLegacyDocArrayBuffer } from "../legacyDocPreviewUtils";

test("detects legacy Word compound document signatures", () => {
  const doc = new Uint8Array([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]);
  assert.equal(isLegacyDocArrayBuffer(doc.buffer), true);

  const docx = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
  assert.equal(isLegacyDocArrayBuffer(docx.buffer), false);
});
