import assert from "node:assert/strict";
import test from "node:test";
import type {
  SessionInputFile,
  SessionInputFilesResponse,
} from "../../../../services/api/session.ts";
import type { ArtifactPart, Message } from "../../../../types/message.ts";
import {
  projectAssistantResponseFiles,
  projectSessionWorkspaceFiles,
  sessionWorkspaceFileToAttachment,
  sessionWorkspaceProjectionForRender,
} from "../sessionWorkspaceFiles.ts";

const inputFile: SessionInputFile = {
  file_id: "file-source",
  run_id: "run-source",
  name: "source.xlsx",
  mime_type:
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  size_bytes: 128,
  preview_url: "/api/ai/files/file-source/preview",
  download_url: "/api/ai/files/file-source/download",
  created_at: "2026-08-01T10:00:00Z",
};

function artifact(
  id: string,
  name: string,
  createdAt: string,
): ArtifactPart {
  return {
    type: "artifact",
    artifact_id: id,
    artifact_type: "result_docx",
    label: name,
    content_type:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    size_bytes: 256,
    preview_url: `/api/ai/artifacts/${id}/preview`,
    download_url: `/api/ai/artifacts/${id}/download`,
    created_at: createdAt,
  };
}

function assistantMessage(...parts: NonNullable<Message["parts"]>): Message {
  return {
    id: "assistant-a",
    role: "assistant",
    content: "done",
    timestamp: new Date("2026-08-02T10:00:00Z"),
    parts,
  };
}

function fulfilledInputs(
  sessionId: string,
  files: SessionInputFile[],
): PromiseFulfilledResult<SessionInputFilesResponse> {
  return { status: "fulfilled", value: { session_id: sessionId, files } };
}

const rejected = (reason: string): PromiseRejectedResult => ({
  status: "rejected",
  reason: new Error(reason),
});

test("merges session inputs and structured assistant response files", () => {
  const report = artifact("artifact-report", "source.xlsx", "2026-08-02T10:00:00Z");
  const projection = projectAssistantResponseFiles(
    projectSessionWorkspaceFiles(
      "session-a",
      fulfilledInputs("session-a", [inputFile]),
    ),
    [assistantMessage(report, report)],
  );

  assert.equal(projection.session_id, "session-a");
  assert.equal(projection.status, "ready");
  assert.deepEqual(projection.inputFiles, [inputFile]);
  assert.deepEqual(
    projection.files.map((file) => file.key),
    ["artifact:artifact-report", "input:file-source"],
  );
  assert.equal(projection.files[0].download_url, report.download_url);
  assert.equal(projection.files[1].download_url, inputFile.download_url);
});

test("supports zero or many response files, including subagent results", () => {
  const base = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-a", []),
  );
  assert.deepEqual(
    projectAssistantResponseFiles(base, [assistantMessage()]).files,
    [],
  );

  const first = artifact("artifact-first", "first.docx", "2026-08-02T10:00:00Z");
  const second = artifact("artifact-second", "second.docx", "2026-08-02T11:00:00Z");
  const projection = projectAssistantResponseFiles(base, [
    assistantMessage(
      first,
      {
        type: "subagent",
        agent_id: "agent-child",
        agent_name: "Child",
        input: "",
        depth: 1,
        status: "complete",
        parts: [second],
      },
    ),
  ]);

  assert.deepEqual(
    projection.files.map((file) => file.key),
    ["artifact:artifact-second", "artifact:artifact-first"],
  );
});

test("does not recover response files from a failed session-wide source", () => {
  const projection = projectSessionWorkspaceFiles(
    "session-a",
    rejected("inputs unavailable"),
  );

  assert.equal(projection.status, "error");
  assert.deepEqual(projection.inputFiles, []);
  assert.deepEqual(projection.files, []);
});

test("rejects another session's fulfilled input projection", () => {
  const projection = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-b", [inputFile]),
  );
  assert.equal(projection.status, "error");
  assert.deepEqual(projection.files, []);
});

test("hides the previous session projection during the first navigation render", () => {
  const projection = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-a", [inputFile]),
  );

  assert.equal(
    sessionWorkspaceProjectionForRender(projection, "session-a"),
    projection,
  );
  assert.deepEqual(
    sessionWorkspaceProjectionForRender(projection, "session-b"),
    {
      session_id: "session-b",
      inputFiles: [],
      files: [],
      status: "loading",
    },
  );
});

test("maps assistant response artifact URLs into the existing attachment viewer", () => {
  const [file] = projectAssistantResponseFiles(
    projectSessionWorkspaceFiles(
      "session-a",
      fulfilledInputs("session-a", []),
    ),
    [
      assistantMessage(
        artifact("artifact-report", "report.docx", "2026-08-02T10:00:00Z"),
      ),
    ],
  ).files;

  assert.deepEqual(sessionWorkspaceFileToAttachment(file), {
    id: "artifact-report",
    key: "artifact:artifact-report",
    name: "report.docx",
    type: "document",
    mimeType:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    size: 256,
    url: "/api/ai/artifacts/artifact-report/preview",
    downloadUrl: "/api/ai/artifacts/artifact-report/download",
  });
});
