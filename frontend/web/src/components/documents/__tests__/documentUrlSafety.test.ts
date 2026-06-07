import test from "node:test";
import assert from "node:assert/strict";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "../documentUrlSafety.ts";

function encodeRepeated(value: string, times: number): string {
  let encoded = value;
  for (let attempt = 0; attempt < times; attempt += 1) {
    encoded = encodeURIComponent(encoded);
  }
  return encoded;
}

test("detects any .claude path segment including encoded artifact urls", () => {
  const encodedSettingsPath = encodeRepeated(".claude/settings.json", 5);

  assert.equal(isSensitiveInternalPath("/workspace/.claude/settings.json"), true);
  assert.equal(
    isSensitiveInternalPath(
      `/api/ai/artifacts/${encodedSettingsPath}/download`,
    ),
    true,
  );
  assert.equal(
    isSensitiveInternalPath("/workspace/project/.claude/checkpoints/ckpt.json"),
    true,
  );
});

test("detects sensitive internal field names in preview URL strings", () => {
  assert.equal(
    isSensitiveInternalPath("/api/ai/artifacts/report/download?storage_key=tenant/private/report.docx"),
    true,
  );
  assert.equal(
    isSensitiveInternalPath(
      "/api/ai/artifacts/report/download?commandSha256=abc123",
    ),
    true,
  );
  assert.equal(
    isSensitiveInternalPath(
      `/api/ai/artifacts/${encodeRepeated("used_skills_source", 3)}/download`,
    ),
    true,
  );
  assert.equal(
    isSensitiveInternalPath(
      "/api/ai/artifacts/report/download?resource-limits=cpu",
    ),
    true,
  );
});

test("detects dot-separated internal metadata keys and bare runtime keys", () => {
  assert.equal(isSensitiveInternalPath("storage.key"), true);
  assert.equal(isSensitiveInternalPath("command.sha256"), true);
  assert.equal(isSensitiveInternalPath("used.skills.source"), true);
  assert.equal(isSensitiveInternalPath("resource.limits"), true);
  assert.equal(isSensitiveInternalPath("runtime.path"), true);
  assert.equal(isSensitiveInternalPath("work.dir"), true);
  assert.equal(isSensitiveInternalPath("runtime"), true);
});

test("authenticated artifact/file allowlist accepts only ai-platform artifact and upload file routes", () => {
  const originalWindow = globalThis.window;
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
      isAllowedAuthenticatedArtifactFileUrl(
        "/api/ai/artifacts/artifact-1/download",
      ),
      true,
    );
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl(
        "/api/ai/artifacts/artifact-1/preview",
      ),
      true,
    );
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl("/api/upload/file/report.png"),
      true,
    );
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl(
        "https://app.example.test/api/upload/file/report.png",
      ),
      true,
    );

    assert.equal(isAllowedAuthenticatedArtifactFileUrl("/api/users"), false);
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl("/api/chat/stream"),
      false,
    );
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl(
        "https://cdn.example.test/report.png",
      ),
      false,
    );
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl(
        "http://127.0.0.1:8020/api/upload/file/report.png",
      ),
      false,
    );
    assert.equal(
      isAllowedAuthenticatedArtifactFileUrl(
        "/api/ai/artifacts/report/download?storage_key=tenant/private/report.docx",
      ),
      false,
    );
  } finally {
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: originalWindow,
    });
  }
});
