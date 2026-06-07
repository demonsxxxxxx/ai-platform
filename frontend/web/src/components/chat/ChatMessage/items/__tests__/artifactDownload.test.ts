import assert from "node:assert/strict";
import test from "node:test";

import { downloadArtifactFile } from "../artifactDownload.ts";

function createFakeDocument(clicks: string[]): Document {
  const anchor = {
    href: "",
    download: "",
    click() {
      clicks.push(this.href);
    },
  };
  return {
    createElement(tagName: string) {
      assert.equal(tagName, "a");
      return anchor;
    },
    body: {
      appendChild() {},
      removeChild() {},
    },
  } as unknown as Document;
}

test("downloadArtifactFile forwards protected platform artifact downloads to the unified selector", async () => {
  const calls: Array<{ url: string; fileName: string }> = [];

  const downloaded = await downloadArtifactFile(
    {
      download_url: "/api/ai/artifacts/artifact-1/download",
      label: "protected.docx",
    },
    {
      downloadPreviewUrl: async (input) => {
        calls.push({ url: input.url, fileName: input.fileName });
      },
    },
  );

  assert.equal(downloaded, true);
  assert.deepEqual(calls, [
    {
      url: "/api/ai/artifacts/artifact-1/download",
      fileName: "protected.docx",
    },
  ]);
});

test("downloadArtifactFile rejects external signed artifact URLs before the unified selector", async () => {
  const calls: Array<{ url: string; fileName: string }> = [];

  const downloaded = await downloadArtifactFile(
    {
      download_url: "https://example.com/signed.docx?token=external",
      label: "external.docx",
    },
    {
      downloadPreviewUrl: async (input) => {
        calls.push({ url: input.url, fileName: input.fileName });
      },
    },
  );

  assert.equal(downloaded, false);
  assert.deepEqual(calls, []);
});

test("downloadArtifactFile rejects encoded internal artifact URLs before the unified selector", async () => {
  const unsafeUrl =
    "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/download";
  const calls: Array<{ url: string; fileName: string }> = [];

  for (const artifact of [
    { download_url: unsafeUrl, label: "secret.txt" },
    { downloadUrl: unsafeUrl, label: "secret.txt" },
  ]) {
    const downloaded = await downloadArtifactFile(artifact, {
      downloadPreviewUrl: async (input) => {
        calls.push({ url: input.url, fileName: input.fileName });
      },
    });

    assert.equal(downloaded, false);
  }

  assert.deepEqual(calls, []);
});

test("downloadArtifactFile does not fetch external signed artifact downloads", async () => {
  const authenticatedCalls: string[] = [];
  const fetchCalls: string[] = [];
  const clicks: string[] = [];
  const revoked: string[] = [];

  const downloaded = await downloadArtifactFile(
    {
      download_url: "https://example.com/signed.docx?token=external",
      label: "external.docx",
    },
    {
      downloadOptions: {
        downloadAuthenticatedFile: async (url) => {
          authenticatedCalls.push(url);
          return { filename: "external.docx", objectUrl: "blob:auth" };
        },
        fetchImpl: async (input) => {
          fetchCalls.push(String(input));
          return new Response("external-file");
        },
        documentRef: createFakeDocument(clicks),
        createObjectURL: () => "blob:external-file",
        revokeObjectURL: (url) => {
          revoked.push(url);
        },
      },
    },
  );

  assert.equal(downloaded, false);
  assert.deepEqual(authenticatedCalls, []);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(clicks, []);
  assert.deepEqual(revoked, []);
});

test("downloadArtifactFile authenticates protected platform artifact downloads", async () => {
  const authenticatedCalls: string[] = [];
  const fetchCalls: string[] = [];

  const downloaded = await downloadArtifactFile(
    {
      download_url: "/api/ai/artifacts/artifact-1/download",
      label: "protected.docx",
    },
    {
      downloadOptions: {
        downloadAuthenticatedFile: async (url) => {
          authenticatedCalls.push(url);
          return { filename: "protected.docx", objectUrl: "blob:auth" };
        },
        fetchImpl: async (input) => {
          fetchCalls.push(String(input));
          return new Response("unexpected");
        },
      },
    },
  );

  assert.equal(downloaded, true);
  assert.deepEqual(authenticatedCalls, ["/api/ai/artifacts/artifact-1/download"]);
  assert.deepEqual(fetchCalls, []);
});
