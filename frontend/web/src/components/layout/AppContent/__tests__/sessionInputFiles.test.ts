import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import type { Message } from "../../../../types/message.ts";
import type { SessionInputFile } from "../../../../services/api/session.ts";
import {
  mergeProjectedSessionFiles,
  sessionInputFileToAttachment,
} from "../sessionInputFiles.ts";

const xlsx: SessionInputFile = {
  file_id: "file-xlsx",
  run_id: "run-source",
  name: "source.xlsx",
  mime_type:
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  size_bytes: 123,
  preview_url:
    "/api/ai/files/file-xlsx/preview?session_id=session-a&run_id=run-source",
  download_url:
    "/api/ai/files/file-xlsx/download?session_id=session-a&run_id=run-source",
};

test("maps a projected input file to independently authorized preview and download URLs", () => {
  assert.deepEqual(sessionInputFileToAttachment(xlsx), {
    id: "file-xlsx",
    key: "file-xlsx",
    name: "source.xlsx",
    type: "document",
    mimeType:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    size: 123,
    url: xlsx.preview_url,
    downloadUrl: xlsx.download_url,
  });
});

test("hydrates a persisted user card only from files bound to that message run", () => {
  const messages: Message[] = [
    {
      id: "msg-source",
      role: "user",
      runId: "run-source",
      content: "analyze it",
      timestamp: new Date(0),
    },
    {
      id: "msg-other",
      role: "user",
      runId: "run-other",
      content: "unrelated",
      timestamp: new Date(1),
    },
  ];

  const merged = mergeProjectedSessionFiles(messages, [xlsx]);

  assert.equal(merged[0].attachments?.[0]?.id, "file-xlsx");
  assert.equal(merged[1].attachments, undefined);
});

test("side panel consumes both session file projections and renders explicit degraded states", () => {
  const source = readFileSync(
    new URL("../../../../librechat-ui/SidePanel.tsx", import.meta.url),
    "utf8",
  );
  const chatView = readFileSync(new URL("../ChatView.tsx", import.meta.url), "utf8");

  assert.match(source, /filesStatus === "error"/);
  assert.match(source, /status === "partial"/);
  assert.match(source, /workbench\.contextPanel\.filesUnavailable/);
  assert.match(source, /onOpenFile/);
  assert.match(source, /onDownloadFile/);
  assert.match(chatView, /sessionApi\.getInputFiles\(sessionId\)/);
  assert.match(chatView, /sessionApi\.getArtifactFiles\(sessionId\)/);
  assert.match(chatView, /projectSessionWorkspaceFiles/);
  assert.match(chatView, /sessionWorkspaceProjectionForRender/);
  assert.match(
    chatView,
    /\[sessionId, currentRunId, messages\.length, attachments\.length\]/,
  );
  assert.match(chatView, /files=\{visibleWorkspaceProjection\.files\}/);
  assert.doesNotMatch(chatView, /<WorkbenchRightPanel[\s\S]{0,400}attachments=\{attachments\}/);
});
