import test from "node:test";
import assert from "node:assert/strict";
import {
  isAllowedRevealArtifactUrl,
  parseProjectRevealSummary,
  sanitizeProjectPath,
} from "../revealPreviewData.ts";

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

test("historical project summaries retain only navigation labels for v1 and v2", () => {
  for (const version of [undefined, 2]) {
    const result = {
      version,
      name: "Demo",
      path: "/workspace/demo",
      files: { "private.txt": "do not hydrate" },
      template: "vue",
    };
    const expected = { projectName: "Demo", projectPath: "/workspace/demo" };
    assert.deepEqual(parseProjectRevealSummary({ args: {}, result }), expected);
    assert.deepEqual(
      parseProjectRevealSummary({ args: {}, result: JSON.stringify(result) }),
      expected,
    );
  }
});

test("historical navigation never reads project files or manifest URLs", () => {
  const result = {
    name: "Demo",
    path: "demo",
    get files(): never {
      throw new Error("files must not be read");
    },
  };
  assert.deepEqual(parseProjectRevealSummary({ args: {}, result }), {
    projectName: "Demo",
    projectPath: "demo",
  });
});

test("historical project navigation handles missing, malformed and failed results", () => {
  assert.deepEqual(parseProjectRevealSummary({ args: { name: "Fallback" } }), {
    projectName: "Fallback",
    projectPath: "",
  });
  for (const result of [
    "broken", "null", "[]", "1",
    { error: "failed", name: "Ignored" },
    { name: 42, path: {} },
  ]) {
    assert.deepEqual(parseProjectRevealSummary({ args: {}, result }), {
      projectName: "",
      projectPath: "",
    });
  }
});

test("historical paths and current file links retain sensitive-path filtering", () => {
  let path = ".claude/runs/private.txt";
  for (let depth = 0; depth <= 5; depth += 1) {
    assert.equal(sanitizeProjectPath(path), "");
    const summary = parseProjectRevealSummary({
      args: {},
      result: { name: "Demo", path },
    });
    assert.equal(summary.projectPath, "");
    path = encodeURIComponent(path);
  }
  assert.equal(sanitizeProjectPath("report.pdf"), "report.pdf");
});
