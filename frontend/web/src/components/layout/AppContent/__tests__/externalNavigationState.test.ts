import test from "node:test";
import assert from "node:assert/strict";
import {
  buildExternalNavigationStateForFile,
  buildExternalNavigationPreviewRequest,
  getExternalNavigationTargetFile,
  shouldResetExternalNavigateFlag,
  shouldScrollToBottomAfterExternalNavigation,
} from "../externalNavigationState.ts";

function encodeRepeated(value: string, times: number): string {
  let encoded = value;
  for (let attempt = 0; attempt < times; attempt += 1) {
    encoded = encodeURIComponent(encoded);
  }
  return encoded;
}

test("resets the external navigation flag only when present", () => {
  assert.equal(
    shouldResetExternalNavigateFlag({ externalNavigate: true }),
    true,
  );
  assert.equal(
    shouldResetExternalNavigateFlag({ externalNavigate: false }),
    false,
  );
  assert.equal(shouldResetExternalNavigateFlag({}), false);
  assert.equal(shouldResetExternalNavigateFlag(null), false);
});

test("marks external navigation requests that should scroll to bottom", () => {
  assert.equal(
    shouldScrollToBottomAfterExternalNavigation({
      externalNavigate: true,
      scrollToBottom: true,
    }),
    true,
  );
  assert.equal(
    shouldScrollToBottomAfterExternalNavigation({
      externalNavigate: true,
      scrollToBottom: false,
    }),
    false,
  );
  assert.equal(
    shouldScrollToBottomAfterExternalNavigation({
      externalNavigate: false,
      scrollToBottom: true,
    }),
    false,
  );
  assert.equal(shouldScrollToBottomAfterExternalNavigation(null), false);
});

test("extracts the target file only for external navigation", () => {
  assert.deepEqual(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {
        fileId: "file-123",
        originalPath: "/tmp/demo.txt",
        traceId: "trace-123",
        source: "reveal_file",
      },
    }),
    {
      fileId: "file-123",
      traceId: "trace-123",
      source: "reveal_file",
    },
  );
  assert.equal(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {},
    }),
    null,
  );
  assert.equal(
    getExternalNavigationTargetFile({
      externalNavigate: false,
      targetFile: {
        fileId: "file-123",
      },
    }),
    null,
  );
  assert.equal(getExternalNavigationTargetFile(null), null);
});

test("drops legacy raw path fields when reading external navigation target file", () => {
  assert.deepEqual(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {
        fileId: "file-legacy-1",
        fileKey: "revealed/.claude/skills/private.pdf",
        fileName: ".claude/skills/private.pdf",
        originalPath: "/workspace/.claude/runs/run-1/private.pdf",
        traceId: "trace-legacy-1",
        source: "reveal_file",
      },
    }),
    {
      fileId: "file-legacy-1",
      traceId: "trace-legacy-1",
      source: "reveal_file",
    },
  );

  assert.equal(
    getExternalNavigationTargetFile({
      externalNavigate: true,
      targetFile: {
        fileKey: "revealed/.claude/skills/private.pdf",
        fileName: "private.pdf",
        originalPath: "/workspace/private.pdf",
      },
    }),
    null,
  );
});

test("builds a file preview request for external navigation", () => {
  assert.deepEqual(
    buildExternalNavigationPreviewRequest({
      id: "file-1",
      file_key: "revealed/file-1",
      file_name: "demo.txt",
      file_size: 128,
      url: "/api/upload/file/revealed/file-1",
      source: "reveal_file",
      original_path: "/tmp/demo.txt",
      project_meta: null,
    }),
    {
      kind: "file",
      previewKey: "external-file:file-1",
      filePath: "/tmp/demo.txt",
      s3Key: "revealed/file-1",
      signedUrl: "/api/upload/file/revealed/file-1",
      fileSize: 128,
    },
  );
});

test("keeps allowed platform URLs for external navigation file previews", () => {
  assert.deepEqual(
    buildExternalNavigationPreviewRequest({
      id: "file-api-1",
      file_key: "revealed/file-api-1",
      file_name: "api.pdf",
      file_size: 128,
      url: "/api/upload/file/revealed/file-api-1",
      source: "reveal_file",
      original_path: "/tmp/api.pdf",
      project_meta: null,
    }),
    {
      kind: "file",
      previewKey: "external-file:file-api-1",
      filePath: "/tmp/api.pdf",
      s3Key: "revealed/file-api-1",
      signedUrl: "/api/upload/file/revealed/file-api-1",
      fileSize: 128,
    },
  );

  assert.deepEqual(
    buildExternalNavigationPreviewRequest({
      id: "file-https-1",
      file_key: "revealed/file-https-1",
      file_name: "https.pdf",
      file_size: 256,
      url: "https://cdn.example.com/plain.pdf",
      source: "reveal_file",
      original_path: "/tmp/https.pdf",
      project_meta: null,
    }),
    {
      kind: "file",
      previewKey: "external-file:file-https-1",
      filePath: "/tmp/https.pdf",
      s3Key: "revealed/file-https-1",
      fileSize: 256,
    },
  );
});

test("keeps revealed XLSX preview and download URLs distinct", () => {
  const preview = buildExternalNavigationPreviewRequest({
    id: "file-xlsx",
    file_key: "revealed/file-xlsx",
    file_name: "checks.xlsx",
    file_size: 128,
    url: "/api/ai/artifacts/file-xlsx/preview",
    preview_url: "/api/ai/artifacts/file-xlsx/preview",
    download_url: "/api/ai/artifacts/file-xlsx/download",
    mime_type:
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    source: "reveal_file",
    original_path: "checks.xlsx",
    project_meta: null,
  });

  assert.equal(preview?.kind, "file");
  if (preview?.kind !== "file") {
    throw new Error("expected a file preview");
  }
  assert.equal(preview.previewUrl, "/api/ai/artifacts/file-xlsx/preview");
  assert.equal(preview.downloadUrl, "/api/ai/artifacts/file-xlsx/download");
  assert.equal(preview.signedUrl, preview.previewUrl);
  assert.equal(
    preview.mimeType,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  );
});

test("omits disallowed signed URLs for external navigation file previews", () => {
  const httpPreview = buildExternalNavigationPreviewRequest({
    id: "file-http-1",
    file_key: "revealed/file-http-1",
    file_name: "http.pdf",
    file_size: 512,
    url: "http://cdn.example.com/plain.pdf",
    source: "reveal_file",
    original_path: "/tmp/http.pdf",
    project_meta: null,
  });

  assert.equal(httpPreview?.kind, "file");
  if (httpPreview?.kind !== "file") {
    throw new Error("expected file preview");
  }
  assert.equal(httpPreview.signedUrl, undefined);

  const encodedSkillsUrl = encodeRepeated(".claude/skills/private.pdf", 5);
  const internalPreview = buildExternalNavigationPreviewRequest({
    id: "file-internal-1",
    file_key: "revealed/file-internal-1",
    file_name: "internal.pdf",
    file_size: 1024,
    url: `https://cdn.example.com/${encodedSkillsUrl}`,
    source: "reveal_file",
    original_path: "/tmp/internal.pdf",
    project_meta: null,
  });

  assert.equal(internalPreview?.kind, "file");
  if (internalPreview?.kind !== "file") {
    throw new Error("expected file preview");
  }
  assert.equal(internalPreview.signedUrl, undefined);
});

test("sanitizes sensitive non-project external preview path and s3 key", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/run-1/private.pdf", 4);
  const encodedSkillsKey = encodeRepeated(".claude/skills/private.pdf", 5);

  assert.deepEqual(
    buildExternalNavigationPreviewRequest({
      id: "file-sensitive-1",
      file_key: `revealed/${encodedSkillsKey}`,
      file_name: "safe-report.pdf",
      file_size: 2048,
      url: `https://cdn.example.com/${encodedRunsPath}`,
      source: "reveal_file",
      original_path: `/workspace/${encodedRunsPath}`,
      project_meta: null,
    }),
    {
      kind: "file",
      previewKey: "external-file:file-sensitive-1",
      filePath: "safe-report.pdf",
      fileSize: 2048,
    },
  );
});

test("uses an empty safe path when all non-project external path labels are sensitive", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/run-2/private.txt", 4);
  const encodedSkillsName = encodeRepeated(".claude/skills/private.txt", 4);

  assert.deepEqual(
    buildExternalNavigationPreviewRequest({
      id: "file-sensitive-2",
      file_key: encodedRunsPath,
      file_name: encodedSkillsName,
      file_size: 64,
      url: null,
      source: "reveal_file",
      original_path: `/workspace/${encodedRunsPath}`,
      project_meta: null,
    }),
    {
      kind: "file",
      previewKey: "external-file:file-sensitive-2",
      filePath: "",
      fileSize: 64,
    },
  );
});

test("does not auto-open preview for externally opened image files", () => {
  assert.deepEqual(
    buildExternalNavigationStateForFile({
      id: "file-image-1",
      file_key: "revealed/file-image-1",
      file_name: "diagram.png",
      file_size: 256,
      url: "/api/upload/file/revealed/file-image-1",
      source: "reveal_file",
      original_path: "/tmp/diagram.png",
      trace_id: "",
      project_meta: null,
    }),
    {
      externalNavigate: true,
      targetFile: {
        fileId: "file-image-1",
        source: "reveal_file",
      },
    },
  );
});

test("stores only sanitized identifiers even when source paths are sensitive images", () => {
  const encodedRunsImagePath = encodeRepeated(
    ".claude/runs/run-3/private.png",
    4,
  );

  assert.deepEqual(
    buildExternalNavigationStateForFile({
      id: "file-sensitive-image-1",
      file_key: "revealed/file-sensitive-image-1",
      file_name: "safe-notes.txt",
      file_size: 32,
      url: "/api/upload/file/revealed/file-sensitive-image-1",
      source: "reveal_file",
      original_path: `/workspace/${encodedRunsImagePath}`,
      trace_id: "",
      project_meta: null,
    }),
    {
      externalNavigate: true,
      targetFile: {
        fileId: "file-sensitive-image-1",
        source: "reveal_file",
      },
    },
  );
});

test("sanitizes target file identifiers before storing external navigation state", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/run-4/private.pdf", 4);
  const encodedSkillsKey = encodeRepeated(".claude/skills/private.pdf", 5);

  const state = buildExternalNavigationStateForFile({
    id: "file-sensitive-state-1",
    file_key: `revealed/${encodedSkillsKey}`,
    file_name: "safe-report.pdf",
    file_size: 128,
    url: "/api/upload/file/revealed/safe-report.pdf",
    source: "reveal_file",
    original_path: `/workspace/${encodedRunsPath}`,
    trace_id: "trace-sensitive-state-1",
    project_meta: null,
  });

  assert.deepEqual(state.targetFile, {
    fileId: "file-sensitive-state-1",
    source: "reveal_file",
    traceId: "trace-sensitive-state-1",
  });
  assert.doesNotMatch(JSON.stringify(state), /\.claude|%252Eclaude/);
});

test("does not store preview payload for non-image external navigation files", () => {
  assert.deepEqual(
    buildExternalNavigationStateForFile({
      id: "file-text-1",
      file_key: "revealed/file-text-1",
      file_name: "notes.txt",
      file_size: 64,
      url: "/api/upload/file/revealed/file-text-1",
      source: "reveal_file",
      original_path: "/tmp/notes.txt",
      trace_id: "",
      project_meta: null,
    }),
    {
      externalNavigate: true,
      targetFile: {
        fileId: "file-text-1",
        source: "reveal_file",
      },
    },
  );
});

test("does not store external preview payloads in router state", () => {
  const state = buildExternalNavigationStateForFile({
    id: "file-router-state-1",
    file_key: "revealed/file-router-state-1",
    file_name: "notes.txt",
    file_size: 64,
    url: "/api/upload/file/revealed/file-router-state-1",
    source: "reveal_file",
    original_path: "/tmp/notes.txt",
    trace_id: "trace-router-state-1",
    project_meta: null,
  });

  assert.deepEqual(state, {
    externalNavigate: true,
    targetFile: {
      fileId: "file-router-state-1",
      source: "reveal_file",
      traceId: "trace-router-state-1",
    },
  });
  assert.doesNotMatch(
    JSON.stringify(state),
    /targetPreview|file_key|file_name|original_path|signedUrl|s3Key|\/tmp\/notes/,
  );
});

test("builds a project preview request for external navigation", () => {
  assert.deepEqual(
    buildExternalNavigationPreviewRequest({
      id: "file-2",
      file_key: "revealed/project-1",
      file_name: "demo-app",
      file_size: 0,
      url: null,
      source: "reveal_project",
      original_path: "/workspace/demo-app",
      project_meta: {
        template: "vanilla",
        entry: "index.html",
        file_count: 1,
        files: {
          "index.html": {
            url: "/api/upload/file/demo/index.html",
            size: 42,
            is_binary: false,
            content_type: "text/html",
          },
        },
      },
    }),
    {
      kind: "project",
      previewKey: "external-project:file-2",
      project: {
        version: 2,
        name: "demo-app",
        mode: "project",
        path: "/workspace/demo-app",
        template: "vanilla",
        entry: "index.html",
        fileCount: 1,
        files: {
          "index.html": {
            url: "/api/upload/file/demo/index.html",
            size: 42,
            is_binary: false,
            content_type: "text/html",
          },
        },
      },
    },
  );
});

test("sanitizes project_meta before building an external project preview request", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/run-1/private.txt", 4);
  const encodedRunsUrl = encodeRepeated(".claude/runs/run-1/private.txt", 5);

  const preview = buildExternalNavigationPreviewRequest({
    id: "file-3",
    file_key: "revealed/project-2",
    file_name: "secure-app",
    file_size: 0,
    url: null,
    source: "reveal_project",
    original_path: `/workspace/${encodeRepeated(".claude/skills/private", 2)}`,
    project_meta: {
      template: "react",
      entry: `/workspace/${encodeRepeated(".claude/runs/run-1/index.html", 4)}`,
      file_count: 99,
      files: {
        "index.html": {
          url: "/api/upload/file/secure/index.html",
          size: 42,
          is_binary: false,
          content_type: "text/html",
        },
        "src/logo.png": {
          url: "https://cdn.example.com/logo.png",
          size: 12,
          is_binary: true,
          content_type: "image/png",
        },
        "src/http.png": {
          url: "http://cdn.example.com/http.png",
          size: 13,
          is_binary: true,
          content_type: "image/png",
        },
        [`src/${encodedRunsPath}`]: {
          url: "/api/upload/file/secure/private.txt",
          size: 9,
          is_binary: false,
        },
        "src/encoded-url.txt": {
          url: `/api/upload/file/secure/${encodedRunsUrl}`,
          size: 10,
          is_binary: false,
        },
      },
    },
  });

  assert.equal(preview?.kind, "project");
  if (preview?.kind !== "project") {
    throw new Error("expected project preview");
  }

  assert.equal(preview.project.version, 2);
  if (preview.project.version !== 2) {
    throw new Error("expected v2 project preview");
  }

  assert.equal(preview.project.path, undefined);
  assert.equal(preview.project.entry, undefined);
  assert.equal(preview.project.fileCount, 1);
  assert.deepEqual(Object.keys(preview.project.files).sort(), ["index.html"]);
  assert.equal(
    preview.project.files["index.html"].url,
    "/api/upload/file/secure/index.html",
  );
});
