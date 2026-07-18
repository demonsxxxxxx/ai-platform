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

test("side panel consumes the persistent projection and renders an explicit degraded state", () => {
  const source = readFileSync(
    new URL("../../../../librechat-ui/SidePanel.tsx", import.meta.url),
    "utf8",
  );
  const chatView = readFileSync(new URL("../ChatView.tsx", import.meta.url), "utf8");

  assert.match(source, /sessionFilesStatus === "error"/);
  assert.match(source, /Session files are temporarily unavailable/);
  assert.match(source, /onOpenSessionFile/);
  assert.match(source, /onDownloadSessionFile/);
  assert.match(chatView, /sessionApi[\s\S]*\.getInputFiles\(sessionId\)/);
  assert.match(chatView, /sessionFiles=\{sessionFiles\}/);
  assert.doesNotMatch(chatView, /<WorkbenchRightPanel[\s\S]{0,400}attachments=\{attachments\}/);
});
