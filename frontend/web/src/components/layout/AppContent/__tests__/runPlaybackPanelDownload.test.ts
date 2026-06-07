import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { downloadRunPlaybackArtifact } from "../runPlaybackDownload.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));

function readRunPlaybackPanelSource(): string {
  return readFileSync(resolve(__dirname, "../RunPlaybackPanel.tsx"), "utf8");
}

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

test("artifact downloads and previews avoid bare protected URL anchors", () => {
  const source = readRunPlaybackPanelSource();

  assert.match(source, /downloadRunPlaybackArtifact/);
  assert.match(source, /openRunPlaybackArtifactPreview/);
  assert.doesNotMatch(
    source,
    /<a[\s\S]{0,300}href=\{artifact\.downloadUrl\}/,
  );
  assert.doesNotMatch(
    source,
    /<a[\s\S]{0,300}href=\{artifact\.previewUrl\}/,
  );
});

test("downloadRunPlaybackArtifact delegates to authenticated blob download", async () => {
  const calls: Array<{ url: string; filename: string }> = [];

  const didDownload = await downloadRunPlaybackArtifact(
    {
      downloadUrl: "/api/ai/artifacts/artifact-1/download",
      label: "report.docx",
    },
    {
      downloadPreviewUrl: async ({ url, fileName }) => {
        calls.push({ url, filename: fileName });
      },
    },
  );

  assert.equal(didDownload, true);
  assert.deepEqual(calls, [
    {
      url: "/api/ai/artifacts/artifact-1/download",
      filename: "report.docx",
    },
  ]);
});

test("downloadRunPlaybackArtifact rejects external signed artifact downloads", async () => {
  const calls: Array<{ url: string; filename: string }> = [];

  const didDownload = await downloadRunPlaybackArtifact(
    {
      downloadUrl: "https://example.com/signed.xlsx?token=external",
      label: "external.xlsx",
    },
    {
      downloadPreviewUrl: async ({ url, fileName }) => {
        calls.push({ url, filename: fileName });
      },
    },
  );

  assert.equal(didDownload, false);
  assert.deepEqual(calls, []);
});

test("downloadRunPlaybackArtifact rejects external signed URLs without native fetch fallback", async () => {
  const authenticatedCalls: string[] = [];
  const fetchCalls: string[] = [];
  const clicks: string[] = [];

  const didDownload = await downloadRunPlaybackArtifact(
    {
      downloadUrl: "https://example.com/signed.xlsx?token=external",
      label: "external.xlsx",
    },
    {
      downloadOptions: {
        downloadAuthenticatedFile: async (url) => {
          authenticatedCalls.push(url);
          return { filename: "external.xlsx", objectUrl: "blob:auth" };
        },
        fetchImpl: async (input) => {
          fetchCalls.push(String(input));
          return new Response("external-file");
        },
        documentRef: createFakeDocument(clicks),
        createObjectURL: () => "blob:external-file",
        revokeObjectURL: () => {},
      },
    },
  );

  assert.equal(didDownload, false);
  assert.deepEqual(authenticatedCalls, []);
  assert.deepEqual(fetchCalls, []);
  assert.deepEqual(clicks, []);
});

test("downloadRunPlaybackArtifact rejects arbitrary same-origin api downloads", async () => {
  const calls: Array<{ url: string; filename: string }> = [];

  const didDownload = await downloadRunPlaybackArtifact(
    {
      downloadUrl: "/api/users/export",
      label: "users.json",
    },
    {
      downloadPreviewUrl: async ({ url, fileName }) => {
        calls.push({ url, filename: fileName });
      },
    },
  );

  assert.equal(didDownload, false);
  assert.deepEqual(calls, []);
});

test("downloadRunPlaybackArtifact authenticates protected platform artifact downloads", async () => {
  const authenticatedCalls: string[] = [];
  const fetchCalls: string[] = [];

  const didDownload = await downloadRunPlaybackArtifact(
    {
      downloadUrl: "/api/ai/artifacts/artifact-1/download",
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

  assert.equal(didDownload, true);
  assert.deepEqual(authenticatedCalls, ["/api/ai/artifacts/artifact-1/download"]);
  assert.deepEqual(fetchCalls, []);
});

test("downloadRunPlaybackArtifact skips artifacts without a download URL", async () => {
  let called = false;

  const didDownload = await downloadRunPlaybackArtifact(
    {
      downloadUrl: null,
      label: "report.docx",
    },
    {
      downloadPreviewUrl: async () => {
        called = true;
      },
    },
  );

  assert.equal(didDownload, false);
  assert.equal(called, false);
});
