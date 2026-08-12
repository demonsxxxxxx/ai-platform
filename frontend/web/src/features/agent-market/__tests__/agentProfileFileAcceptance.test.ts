import assert from "node:assert/strict";
import test from "node:test";

import {
  isAcceptedProfileFile,
  partitionAcceptedProfileFiles,
} from "../../../hooks/useFileUpload.ts";
import { resolveAgentAcceptedFileTypes } from "../../../components/layout/AppContent/agentProfileFileTypes.ts";

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

test("unsupported Agent files do not consume the accepted upload count", () => {
  const pdf = { name: "report.pdf", type: "application/pdf" } as File;
  const script = { name: "run.js", type: "text/javascript" } as File;

  const result = partitionAcceptedProfileFiles([script, pdf], ["application/pdf"]);

  assert.deepEqual(result.accepted, [pdf]);
  assert.deepEqual(result.rejected, [script]);
});

test("Agent drag-and-drop and composer share one accepted-file policy", () => {
  assert.equal(resolveAgentAcceptedFileTypes(undefined), undefined);
  assert.deepEqual(
    resolveAgentAcceptedFileTypes({
      supported_input_types: ["text"],
      supported_file_types: ["application/pdf"],
    }),
    [],
  );
  assert.deepEqual(
    resolveAgentAcceptedFileTypes({
      supported_input_types: ["text", "file"],
      supported_file_types: ["application/pdf"],
    }),
    ["application/pdf"],
  );
});
