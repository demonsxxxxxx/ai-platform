import assert from "node:assert/strict";
import test from "node:test";

import { isLegacyDocFile, isWordPreviewFile } from "../index";

test("treats legacy .doc files as Word preview candidates", () => {
  assert.equal(isWordPreviewFile("doc"), true);
  assert.equal(isWordPreviewFile("docx"), true);
  assert.equal(isLegacyDocFile("doc"), false);
});
