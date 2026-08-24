import assert from "node:assert/strict";
import test from "node:test";
import {
  buildSessionArtifactFilesUrl,
  sessionApi,
} from "../session.ts";

test("builds the exact session artifact projection url", () => {
  assert.equal(
    buildSessionArtifactFilesUrl("session/a"),
    "/api/files/revealed/session/session%2Fa",
  );
});

test("loads more than 500 artifacts in one session projection", async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  const item = (
    id: string,
    source: "reveal_file" | "reveal_project" = "reveal_file",
  ) => ({
    id,
    file_name: `${id}.docx`,
    file_type: source === "reveal_project" ? "project" : "document",
    mime_type:
      source === "reveal_project"
        ? null
        : "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    file_size: 10,
    preview_url: source === "reveal_project" ? null : `/preview/${id}`,
    download_url: source === "reveal_project" ? null : `/download/${id}`,
    session_id: "session/a",
    source,
    created_at: "2026-08-01T00:00:00Z",
  });
  globalThis.fetch = (async (input) => {
    calls.push(String(input));
    return new Response(
      JSON.stringify([
        ...Array.from({ length: 501 }, (_, index) => item(`artifact-${index}`)),
        item("project-1", "reveal_project"),
      ]),
      { status: 200 },
    );
  }) as typeof fetch;

  try {
    const projection = await sessionApi.getArtifactFiles("session/a");
    assert.equal(projection.session_id, "session/a");
    assert.equal(projection.files.length, 501);
    assert.equal(
      projection.files.some((file) => file.source === "reveal_project"),
      false,
    );
    assert.deepEqual(calls, [
      "/api/files/revealed/session/session%2Fa",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("fails closed when an artifact response contains another session", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(
      JSON.stringify([
        {
          id: "artifact-other",
          file_name: "other.docx",
          file_type: "document",
          mime_type:
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          file_size: 10,
          preview_url: "/preview/other",
          download_url: "/download/other",
          session_id: "session-b",
          source: "reveal_file",
          created_at: "2026-08-01T00:00:00Z",
        },
      ]),
      { status: 200 },
    )) as typeof fetch;

  try {
    await assert.rejects(
      sessionApi.getArtifactFiles("session-a"),
      /session_artifact_projection_mismatch/,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
