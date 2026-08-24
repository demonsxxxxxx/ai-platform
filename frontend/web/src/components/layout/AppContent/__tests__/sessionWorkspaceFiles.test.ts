import assert from "node:assert/strict";
import test from "node:test";
import type {
  SessionArtifactFile,
  SessionArtifactFilesResponse,
  SessionInputFile,
  SessionInputFilesResponse,
} from "../../../../services/api/session.ts";
import {
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
): SessionArtifactFile {
  return {
    id,
    file_name: name,
    file_type: "document",
    mime_type:
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    file_size: 256,
    preview_url: `/api/ai/artifacts/${id}/preview`,
    download_url: `/api/ai/artifacts/${id}/download`,
    session_id: "session-a",
    source: "reveal_file",
    created_at: createdAt,
  };
}

function fulfilledInputs(
  sessionId: string,
  files: SessionInputFile[],
): PromiseFulfilledResult<SessionInputFilesResponse> {
  return { status: "fulfilled", value: { session_id: sessionId, files } };
}

function fulfilledArtifacts(
  sessionId: string,
  files: SessionArtifactFile[],
): PromiseFulfilledResult<SessionArtifactFilesResponse> {
  return { status: "fulfilled", value: { session_id: sessionId, files } };
}

const rejected = (reason: string): PromiseRejectedResult => ({
  status: "rejected",
  reason: new Error(reason),
});

test("merges session inputs and artifacts without collapsing same-name files", () => {
  const report = artifact("artifact-report", "source.xlsx", "2026-08-02T10:00:00Z");
  const projection = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-a", [inputFile]),
    fulfilledArtifacts("session-a", [report, report]),
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

test("keeps the successful source and marks partial projection failures", () => {
  const report = artifact("artifact-report", "report.docx", "2026-08-02T10:00:00Z");
  const projection = projectSessionWorkspaceFiles(
    "session-a",
    rejected("inputs unavailable"),
    fulfilledArtifacts("session-a", [report]),
  );

  assert.equal(projection.status, "partial");
  assert.deepEqual(projection.inputFiles, []);
  assert.deepEqual(projection.files.map((file) => file.id), ["artifact-report"]);
});

test("rejects another session's fulfilled projection and distinguishes total failure", () => {
  const partial = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-b", [inputFile]),
    fulfilledArtifacts("session-a", []),
  );
  assert.equal(partial.status, "partial");
  assert.deepEqual(partial.files, []);

  const failed = projectSessionWorkspaceFiles(
    "session-a",
    rejected("inputs unavailable"),
    rejected("artifacts unavailable"),
  );
  assert.equal(failed.status, "error");
  assert.deepEqual(failed.files, []);
});

test("hides the previous session projection during the first navigation render", () => {
  const projection = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-a", [inputFile]),
    fulfilledArtifacts("session-a", [
      artifact("artifact-report", "report.docx", "2026-08-02T10:00:00Z"),
    ]),
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

test("maps artifact preview and download URLs into the existing attachment viewer", () => {
  const [file] = projectSessionWorkspaceFiles(
    "session-a",
    fulfilledInputs("session-a", []),
    fulfilledArtifacts("session-a", [
      artifact("artifact-report", "report.docx", "2026-08-02T10:00:00Z"),
    ]),
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
