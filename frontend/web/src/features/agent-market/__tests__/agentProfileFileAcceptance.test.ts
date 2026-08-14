import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import {
  isAcceptedProfileFile,
  partitionAcceptedProfileFiles,
} from "../../../hooks/useFileUpload.ts";

test("generic upload inputs can apply an explicit platform MIME or extension allowlist", () => {
  const pdf = { name: "report.PDF", type: "application/pdf" };
  const image = { name: "scan.png", type: "image/png" };
  const script = { name: "run.js", type: "text/javascript" };

  assert.equal(isAcceptedProfileFile(pdf, undefined), true, "ordinary Chat stays unchanged");
  assert.equal(isAcceptedProfileFile(pdf, []), false, "an explicit empty platform policy rejects files");
  assert.equal(isAcceptedProfileFile(pdf, [".pdf"]), true);
  assert.equal(isAcceptedProfileFile(pdf, ["application/pdf"]), true);
  assert.equal(isAcceptedProfileFile(image, ["image/*"]), true);
  assert.equal(isAcceptedProfileFile(script, ["application/pdf", ".docx"]), false);
});

test("files rejected by a platform policy do not consume the accepted upload count", () => {
  const pdf = { name: "report.pdf", type: "application/pdf" } as File;
  const script = { name: "run.js", type: "text/javascript" } as File;

  const result = partitionAcceptedProfileFiles([script, pdf], ["application/pdf"]);

  assert.deepEqual(result.accepted, [pdf]);
  assert.deepEqual(result.rejected, [script]);
});

test("Expert attachments are optional context and profiles never configure upload formats", () => {
  const chatSource = readFileSync(
    join(process.cwd(), "src/components/layout/AppContent/ChatAppContent.tsx"),
    "utf8",
  );
  const viewSource = readFileSync(
    join(process.cwd(), "src/components/layout/AppContent/ChatView.tsx"),
    "utf8",
  );

  assert.match(chatSource, /useDragAndDrop\(\)/);
  assert.match(viewSource, /acceptedFileTypes: undefined/);
  assert.doesNotMatch(`${chatSource}\n${viewSource}`, /supported_file_types|resolveAgentAcceptedFileTypes/);
});
