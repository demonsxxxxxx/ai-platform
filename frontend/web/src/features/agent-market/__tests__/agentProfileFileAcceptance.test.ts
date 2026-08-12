import assert from "node:assert/strict";
import test from "node:test";

import { isAcceptedProfileFile } from "../../../hooks/useFileUpload.ts";

test("Agent file inputs follow the published MIME and extension allowlist", () => {
  const pdf = { name: "report.PDF", type: "application/pdf" };
  const image = { name: "scan.png", type: "image/png" };
  const script = { name: "run.js", type: "text/javascript" };

  assert.equal(isAcceptedProfileFile(pdf, undefined), true, "ordinary Chat stays unchanged");
  assert.equal(isAcceptedProfileFile(pdf, []), false, "text-only Agent rejects files");
  assert.equal(isAcceptedProfileFile(pdf, [".pdf"]), true);
  assert.equal(isAcceptedProfileFile(pdf, ["application/pdf"]), true);
  assert.equal(isAcceptedProfileFile(image, ["image/*"]), true);
  assert.equal(isAcceptedProfileFile(script, ["application/pdf", ".docx"]), false);
});
