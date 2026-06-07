import assert from "node:assert/strict";
import test from "node:test";

import {
  buildSanitizedProjectRevealDataFromMeta,
  clearProjectRevealFilesCache,
  getCachedProjectRevealFiles,
  isAllowedRevealArtifactUrl,
  loadProjectRevealFiles,
  loadProjectRevealFilesCached,
  parseFileRevealPreviewData,
  parseProjectRevealSummary,
  shouldShowProjectRevealLoadingError,
} from "../revealPreviewData.ts";

function encodeRepeated(value: string, times: number): string {
  let encoded = value;
  for (let attempt = 0; attempt < times; attempt += 1) {
    encoded = encodeURIComponent(encoded);
  }
  return encoded;
}

test("parseFileRevealPreviewData strips unsafe reveal_file urls and sensitive paths", () => {
  const externalHttp = parseFileRevealPreviewData({
    args: {},
    result: JSON.stringify({
      key: "revealed/http-image",
      url: "http://cdn.example.com/generated.png",
      name: "generated.png",
      type: "image",
      mimeType: "image/png",
      size: 56,
      _meta: { path: "/workspace/generated.png" },
    }),
  });

  assert.equal(externalHttp.filePath, "/workspace/generated.png");
  assert.equal(externalHttp.s3Key, "");
  assert.equal(externalHttp.s3Url, "");

  const encodedRunsPath = encodeRepeated(".claude/runs/run-1/secret.png", 4);
  const encodedRunsUrl = encodeRepeated(".claude/runs/run-1/secret.png", 5);
  const sensitive = parseFileRevealPreviewData({
    args: {},
    result: JSON.stringify({
      key: `revealed/${encodedRunsPath}`,
      url: `/api/ai/artifacts/${encodedRunsUrl}/download`,
      name: "secret.png",
      type: "image",
      mimeType: "image/png",
      size: 12,
      _meta: { path: `/workspace/${encodedRunsPath}` },
    }),
  });

  assert.equal(sensitive.filePath, "");
  assert.equal(sensitive.s3Key, "");
  assert.equal(sensitive.s3Url, "");
  assert.equal(sensitive.fileSize, 12);
});

test("parseFileRevealPreviewData keeps protected api and rejects external https reveal_file urls", () => {
  const protectedApi = parseFileRevealPreviewData({
    args: {},
    result: JSON.stringify({
      key: "revealed/api-image",
      url: "/api/ai/artifacts/api-image/download",
      name: "api-image.png",
      type: "image",
      mimeType: "image/png",
      size: 64,
      _meta: { path: "/workspace/api-image.png" },
    }),
  });

  assert.equal(protectedApi.filePath, "/workspace/api-image.png");
  assert.equal(protectedApi.s3Key, "revealed/api-image");
  assert.equal(protectedApi.s3Url, "/api/ai/artifacts/api-image/download");
  assert.equal(protectedApi.mimeType, "image/png");

  const externalHttps = parseFileRevealPreviewData({
    args: {},
    result: JSON.stringify({
      type: "file_reveal",
      file: {
        path: "/workspace/remote.png",
        s3_url: "https://cdn.example.com/remote.png",
        s3_key: "revealed/remote",
        size: 128,
      },
    }),
  });

  assert.equal(externalHttps.filePath, "/workspace/remote.png");
  assert.equal(externalHttps.s3Key, "");
  assert.equal(externalHttps.s3Url, "");
  assert.equal(externalHttps.fileSize, 128);
});

test("isAllowedRevealArtifactUrl only accepts authenticated artifact and upload file URLs", () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: {
      location: {
        origin: "https://app.example.test",
      },
    },
  });

  try {
    assert.equal(
      isAllowedRevealArtifactUrl("/api/ai/artifacts/report/download"),
      true,
    );
    assert.equal(
      isAllowedRevealArtifactUrl("/api/ai/artifacts/report/preview"),
      true,
    );
    assert.equal(
      isAllowedRevealArtifactUrl("/api/upload/file/report.png"),
      true,
    );
    assert.equal(
      isAllowedRevealArtifactUrl(
        "https://app.example.test/api/ai/artifacts/report/download",
      ),
      true,
    );
    assert.equal(isAllowedRevealArtifactUrl("/api/auth/me"), false);
    assert.equal(
      isAllowedRevealArtifactUrl("/api/ai/runs/run-1/playback"),
      false,
    );
    assert.equal(
      isAllowedRevealArtifactUrl("https://cdn.example.com/report.png"),
      false,
    );
    assert.equal(
      isAllowedRevealArtifactUrl("http://cdn.example.com/report.png"),
      false,
    );
    assert.equal(
      isAllowedRevealArtifactUrl(
        "/api/ai/artifacts/%2Eclaude%2Fruns%2Fsecret/download",
      ),
      false,
    );
  } finally {
    if (originalWindow) {
      Object.defineProperty(globalThis, "window", originalWindow);
    } else {
      delete (globalThis as { window?: Window }).window;
    }
  }
});

test("buildSanitizedProjectRevealDataFromMeta filters raw project_meta files and recounts visible files", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/run-1/private.txt", 4);
  const encodedRunsUrl = encodeRepeated(".claude/runs/run-1/private.txt", 5);

  const project = buildSanitizedProjectRevealDataFromMeta({
    name: "demo-app",
    path: `/workspace/${encodeRepeated(".claude/skills/private", 2)}`,
    meta: {
      mode: "project",
      template: "react",
      entry: `/workspace/${encodeRepeated(".claude/runs/run-1/index.html", 4)}`,
      file_count: 99,
      files: {
        "index.html": {
          url: "/api/upload/file/demo/index.html",
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
          url: "/api/upload/file/demo/private.txt",
          size: 9,
          is_binary: false,
        },
        "src/encoded-url.txt": {
          url: `/api/upload/file/demo/${encodedRunsUrl}`,
          size: 10,
          is_binary: false,
        },
      },
    },
  });

  assert.equal(project?.version, 2);
  if (project?.version !== 2) {
    throw new Error("expected v2 project data");
  }

  assert.equal(project.path, undefined);
  assert.equal(project.entry, undefined);
  assert.equal(project.fileCount, 1);
  assert.deepEqual(Object.keys(project.files).sort(), ["index.html"]);
  assert.equal(project.files["index.html"].url, "/api/upload/file/demo/index.html");
});

test("parses folder mode from reveal_project results", () => {
  const summary = parseProjectRevealSummary({
    args: { project_path: "/workspace/backend-service" },
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "backend-service",
      mode: "folder",
      template: "vanilla",
      files: {
        "/README.md": {
          url: "/api/upload/file/demo-readme",
          is_binary: false,
          size: 10,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.mode, "folder");
});

test("defaults legacy reveal_project results to project mode", () => {
  const summary = parseProjectRevealSummary({
    args: { project_path: "/workspace/site" },
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      entry: "/src/main.jsx",
      files: {
        "/src/main.jsx": {
          url: "/api/upload/file/demo-entry",
          is_binary: false,
          size: 20,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.mode, "project");
});

test("does not mark pure binary reveal_project folders as failed loads", () => {
  const showError = shouldShowProjectRevealLoadingError({
    files: {},
    binaryFiles: {
      "/main.png": "https://example.com/main.png",
      "/detail.png": "https://example.com/detail.png",
    },
    manifestFiles: {
      "/main.png": {
        url: "https://example.com/main.png",
        is_binary: true,
        size: 100,
      },
      "/detail.png": {
        url: "https://example.com/detail.png",
        is_binary: true,
        size: 100,
      },
    },
  });

  assert.equal(showError, false);
});

test("loadProjectRevealFiles uses authenticated document requests for protected v2 text entries", async () => {
  const authenticatedCalls: string[] = [];
  const fetchCalls: string[] = [];

  const loaded = await loadProjectRevealFiles(
    {
      version: 2,
      name: "protected-site",
      mode: "project",
      template: "vanilla",
      fileCount: 1,
      files: {
        "/README.md": {
          url: "/api/ai/artifacts/readme/download",
          is_binary: false,
          size: 11,
        },
      },
    },
    {
      fetchOptions: {
        authenticatedRequest: async (input) => {
          authenticatedCalls.push(String(input));
          return new Response("# Protected");
        },
        fetchImpl: async (input) => {
          fetchCalls.push(String(input));
          return new Response("unexpected-native-fetch");
        },
      },
    },
  );

  assert.deepEqual(authenticatedCalls, ["/api/ai/artifacts/readme/download"]);
  assert.deepEqual(fetchCalls, []);
  assert.equal(loaded.files["/README.md"], "# Protected");
  assert.deepEqual(loaded.failed, []);
});

test("parseProjectRevealSummary strips internal manifest fields from v2 project data", () => {
  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      path: "/workspace/.claude/runs/run-1",
      files: {
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 42,
          content_type: "text/plain",
          storage_key: "tenant/private/app.tsx",
          runtime_path: "/workspace/.claude/skills/site/src/App.tsx",
          work_dir: "/workspace/.claude/runs/run-1",
          command_sha256: "sha256:abc",
          used_skills_source: ".claude/skills/private",
          resource_limits: { cpu: 2 },
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  const serialized = JSON.stringify(summary.parsed);
  assert.doesNotMatch(serialized, /storage_key|runtime_path|work_dir/);
  assert.doesNotMatch(serialized, /command_sha256|used_skills_source/);
  assert.doesNotMatch(serialized, /resource_limits|tenant\/private/);
  assert.doesNotMatch(serialized, /\.claude\/skills/);
});

test("parseProjectRevealSummary rejects sensitive v2 manifest paths and entry values", () => {
  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      entry: "/workspace/.claude/skills/private/src/App.tsx",
      files: {
        "/workspace/.claude/skills/private/src/App.tsx": {
          url: "/api/ai/artifacts/private/download",
          is_binary: false,
          size: 42,
        },
        "/workspace/.claude/runs/run-1/output.txt": {
          url: "/api/ai/artifacts/run-output/download",
          is_binary: false,
          size: 10,
        },
        "/src/runtime.txt": {
          url: "/workspace/.claude/runs/run-1/runtime.txt",
          is_binary: false,
          size: 9,
        },
        "/%2Eclaude/runs/encoded-output.txt": {
          url: "/api/ai/artifacts/encoded-output/download",
          is_binary: false,
          size: 12,
        },
        "/src/encoded-url.txt": {
          url: "/api/ai/artifacts/%2Eclaude%2Fruns%2Fencoded/download",
          is_binary: false,
          size: 13,
        },
        "/src/plain-http.png": {
          url: "http://cdn.example.com/plain-http.png",
          is_binary: true,
          size: 14,
        },
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 11,
        },
        "/src/logo.png": {
          url: "https://cdn.example.com/logo.png",
          is_binary: true,
          size: 99,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  if (summary.parsed?.version !== 2) {
    throw new Error("expected v2 project reveal data");
  }

  assert.equal(summary.parsed.entry, undefined);
  assert.deepEqual(Object.keys(summary.parsed.files).sort(), ["/src/App.tsx"]);
  assert.equal(
    summary.parsed.files["/src/App.tsx"].url,
    "/api/ai/artifacts/app/download",
  );

  const serialized = JSON.stringify(summary.parsed);
  assert.doesNotMatch(serialized, /\.claude\/skills|\.claude\/runs/);
  assert.doesNotMatch(serialized, /%2Eclaude|%2Fruns/i);
  assert.doesNotMatch(serialized, /plain-http/);
  assert.doesNotMatch(serialized, /cdn\.example\.com/);
  assert.doesNotMatch(serialized, /workspace\/\.claude/);
});

test("parseProjectRevealSummary strips encoded sensitive entry values", () => {
  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      entry: "/workspace/%2Eclaude/runs/run-1/src/App.tsx",
      files: {
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 11,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  assert.equal(summary.parsed?.entry, undefined);
});

test("clearProjectRevealFilesCache prevents old in-flight loads from repopulating the cache", async () => {
  clearProjectRevealFilesCache();
  const originalFetch = globalThis.fetch;
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const resolvers: Array<(response: Response) => void> = [];
  const fetchCalls: string[] = [];
  const previewKey = "auth-scoped-project";
  const project = {
    version: 2 as const,
    name: "site",
    mode: "project" as const,
    template: "vanilla" as const,
    fileCount: 1,
    files: {
      "/README.md": {
        url: "/api/ai/artifacts/readme/download",
        is_binary: false,
        size: 11,
      },
    },
  };
  const waitForFetchStart = async () => {
    for (let attempt = 0; attempt < 10; attempt += 1) {
      if (resolvers.length > 0) return;
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
  };

  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: () => null,
      setItem() {},
      removeItem() {},
    },
  });
  globalThis.fetch = (async (input) => {
    fetchCalls.push(String(input));
    return new Promise<Response>((resolve) => {
      resolvers.push(resolve);
    });
  }) as typeof fetch;

  try {
    const firstLoad = loadProjectRevealFilesCached({ previewKey, project });
    await waitForFetchStart();
    assert.equal(fetchCalls.length, 1);

    clearProjectRevealFilesCache();
    resolvers.shift()?.(new Response("old-scope"));
    const firstResult = await firstLoad;

    assert.equal(firstResult.files["/README.md"], "old-scope");
    assert.equal(getCachedProjectRevealFiles(previewKey), null);

    const secondLoad = loadProjectRevealFilesCached({ previewKey, project });
    await waitForFetchStart();
    assert.equal(fetchCalls.length, 2);

    resolvers.shift()?.(new Response("new-scope"));
    const secondResult = await secondLoad;

    assert.equal(secondResult.files["/README.md"], "new-scope");
    assert.equal(
      getCachedProjectRevealFiles(previewKey)?.files["/README.md"],
      "new-scope",
    );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalLocalStorage) {
      Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
    } else {
      delete (globalThis as { localStorage?: Storage }).localStorage;
    }
    clearProjectRevealFilesCache();
  }
});

test("parseProjectRevealSummary strips double-encoded sensitive v2 manifest paths and urls", () => {
  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      files: {
        "/src/%252Eclaude%252Fruns%252Fsecret.txt": {
          url: "/api/ai/artifacts/secret-path/download",
          is_binary: false,
          size: 9,
        },
        "/src/encoded-url.txt": {
          url: "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/download",
          is_binary: false,
          size: 10,
        },
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 11,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  if (summary.parsed?.version !== 2) {
    throw new Error("expected v2 project reveal data");
  }

  assert.deepEqual(Object.keys(summary.parsed.files), ["/src/App.tsx"]);
  const serialized = JSON.stringify(summary.parsed);
  assert.doesNotMatch(serialized, /\.claude\/runs/);
  assert.doesNotMatch(serialized, /%252Eclaude|%252Fruns|%2Eclaude|%2Fruns/i);
});

test("parseProjectRevealSummary strips four-times encoded sensitive v2 manifest paths, urls, and values", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/secret.txt", 4);
  const encodedRunsUrl = encodeRepeated(".claude/runs/secret", 4);
  const encodedSkillsEntry = encodeRepeated(".claude/skills/private/src/App.tsx", 4);

  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      path: `/workspace/${encodedSkillsEntry}`,
      entry: `/workspace/${encodedSkillsEntry}`,
      files: {
        [`/src/${encodedRunsPath}`]: {
          url: "/api/ai/artifacts/secret-path/download",
          is_binary: false,
          size: 9,
        },
        "/src/encoded-url.txt": {
          url: `/api/ai/artifacts/${encodedRunsUrl}/download`,
          is_binary: false,
          size: 10,
        },
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 11,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  if (summary.parsed?.version !== 2) {
    throw new Error("expected v2 project reveal data");
  }

  assert.equal(summary.projectPath, "");
  assert.equal(summary.parsed.path, undefined);
  assert.equal(summary.parsed.entry, undefined);
  assert.deepEqual(Object.keys(summary.parsed.files), ["/src/App.tsx"]);
});

test("parseProjectRevealSummary strips five-times encoded sensitive v2 manifest paths, urls, and values", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/secret.txt", 5);
  const encodedRunsUrl = encodeRepeated(".claude/runs/secret", 5);
  const encodedSkillsEntry = encodeRepeated(".claude/skills/private/src/App.tsx", 5);

  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      path: `/workspace/${encodedSkillsEntry}`,
      entry: `/workspace/${encodedSkillsEntry}`,
      files: {
        [`/src/${encodedRunsPath}`]: {
          url: "/api/ai/artifacts/secret-path/download",
          is_binary: false,
          size: 9,
        },
        "/src/encoded-url.txt": {
          url: `/api/ai/artifacts/${encodedRunsUrl}/download`,
          is_binary: false,
          size: 10,
        },
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 11,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  if (summary.parsed?.version !== 2) {
    throw new Error("expected v2 project reveal data");
  }

  assert.equal(summary.projectPath, "");
  assert.equal(summary.parsed.path, undefined);
  assert.equal(summary.parsed.entry, undefined);
  assert.deepEqual(Object.keys(summary.parsed.files), ["/src/App.tsx"]);
});

test("parseProjectRevealSummary reports v2 fileCount from sanitized visible files", () => {
  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      version: 2,
      name: "site",
      template: "react",
      file_count: 9,
      files: {
        "/workspace/.claude/runs/run-1/private.txt": {
          url: "/api/ai/artifacts/private/download",
          is_binary: false,
          size: 9,
        },
        "/src/App.tsx": {
          url: "/api/ai/artifacts/app/download",
          is_binary: false,
          size: 11,
        },
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 2);
  assert.equal(summary.fileCount, 1);
  assert.equal(summary.parsed?.fileCount, 1);
});

test("parseProjectRevealSummary sanitizes legacy v1 text files and counts only visible files", () => {
  const encodedRunsPath = encodeRepeated(".claude/runs/run-1/secret.txt", 4);
  const encodedSkillsEntry = encodeRepeated(
    ".claude/skills/private/src/App.tsx",
    5,
  );

  const summary = parseProjectRevealSummary({
    args: {},
    result: JSON.stringify({
      type: "project_reveal",
      name: "legacy-site",
      template: "react",
      file_count: 99,
      path: `/workspace/${encodedSkillsEntry}`,
      entry: `/workspace/${encodedSkillsEntry}`,
      files: {
        "/src/App.tsx": "export default function App() {}",
        "/workspace/.claude/skills/private/README.md": "private skill notes",
        [`/src/${encodedRunsPath}`]: "encoded run output",
      },
    }),
    parseErrorMessage: "parse error",
  });

  assert.equal(summary.parsed?.version, 1);
  if (summary.parsed?.version !== 1) {
    throw new Error("expected v1 project reveal data");
  }

  assert.equal(summary.projectPath, "");
  assert.equal(summary.parsed.path, undefined);
  assert.equal(summary.parsed.entry, undefined);
  assert.equal(summary.fileCount, 1);
  assert.equal(summary.parsed.fileCount, 1);
  assert.deepEqual(summary.parsed.files, {
    "/src/App.tsx": "export default function App() {}",
  });
  const serialized = JSON.stringify(summary.parsed);
  assert.doesNotMatch(serialized, /\.claude\/skills|\.claude\/runs/);
  assert.doesNotMatch(serialized, /%2Eclaude|%2Fruns|%252Eclaude/i);
});
