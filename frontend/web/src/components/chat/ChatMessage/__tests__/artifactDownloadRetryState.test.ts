import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const renderer = readFileSync(
  join(root, "src/components/chat/ChatMessage/MessagePartRenderer.tsx"),
  "utf8",
);
const registry = readFileSync(
  join(
    root,
    "src/components/chat/ChatMessage/items/artifactDownloadRegistry.ts",
  ),
  "utf8",
);
test("artifact card wires local retry state without logging download errors", () => {
  assert.match(renderer, /useState/);
  assert.match(renderer, /useRef/);
  assert.match(renderer, /downloadState/);
  assert.match(renderer, /getArtifactDownloadController/);
  assert.match(registry, /createArtifactDownloadScopeContext/);
  assert.match(registry, /entry\.isInFlight/);
  assert.match(registry, /succeeded \? "idle" : "failed"/);
  assert.doesNotMatch(renderer, /console\.(warn|error|log).*Download/i);
});

test("artifact card renders Chinese-first recoverable failure copy and retry action without exposing exceptions", () => {
  assert.match(renderer, /下载失败，请稍后重试。/);
  assert.match(renderer, /重试下载/);
  assert.match(renderer, /common\.retry/);
  assert.match(renderer, /role="alert"/);
  assert.doesNotMatch(renderer, /error\.message|String\(error\)|JSON\.stringify\(error\)/);
});
