import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { resolveInternalAiApplication } from "../aiApplicationCatalog.ts";

const routeSource = readFileSync(resolve(import.meta.dirname, "../AgentApplicationRoute.tsx"), "utf8");

test("internal AI application aliases resolve their dedicated pages", () => {
  assert.deepEqual(resolveInternalAiApplication("sop-assistant"), {
    name: "知识库问答",
  });
  assert.deepEqual(resolveInternalAiApplication("word-review"), {
    name: "文档审核",
  });
  assert.equal(resolveInternalAiApplication("unknown"), undefined);
});

test("application routes render task-specific surfaces and use the independent Word review service", () => {
  assert.match(routeSource, /您好，我是 SOP 问询助手/);
  assert.match(routeSource, /处理记录/);
  assert.match(routeSource, /review-page/);
  assert.match(routeSource, /review-hero/);
  assert.match(routeSource, /review-workbench/);
  assert.match(routeSource, /当前排队/);
  assert.match(routeSource, /累计审核文档/);
  assert.match(routeSource, /当前任务/);
  assert.match(routeSource, /历史记录/);
  assert.match(routeSource, /SOP_RAGFLOW_API_BASE = "http:\/\/10\.56\.0\.211:8080"/);
  assert.match(routeSource, /VITE_RAGFLOW_SOP_SHARE_URL/);
  assert.match(routeSource, /\/api\/v1\/chatbots\/\$\{encodeURIComponent\(config\.chatId\)\}\/completions/);
  assert.match(routeSource, /question: "初始化会话", stream: true/);
  assert.match(routeSource, /sessionId \|\| \(await createSopSession\(config, controller\.signal\)\)/);
  assert.match(routeSource, /session_id: activeSessionId/);
  assert.match(routeSource, /Authorization: `Bearer \$\{config\.auth\}`/);
  assert.match(routeSource, /Accept: "text\/event-stream"/);
  assert.doesNotMatch(routeSource, /useAgent\(|getPublished\(|agentProfileApi|sendMessage\(/);
  assert.match(routeSource, /VITE_WORD_REVIEW_API_TARGET/);
  assert.match(routeSource, /http:\/\/10\.56\.0\.211:8014/);
  assert.match(routeSource, /\/api\/upload/);
  assert.match(routeSource, /\/api\/chat\/stream/);
  assert.match(routeSource, /\/api\/review\/stats\/documents/);
  assert.match(routeSource, /\/api\/review\/history/);
  assert.match(routeSource, /\/api\/file\/view/);
  assert.match(routeSource, /\/api\/file\/download/);
  assert.match(routeSource, /form\.append\("file", file\)/);
  assert.match(routeSource, /form\.append\("workspace_id", context\.workspaceId\)/);
  assert.match(routeSource, /file_id: task\.fileId/);
  assert.match(routeSource, /client_task_id: task\.localId/);
  assert.match(routeSource, /Accept: "text\/event-stream"/);
  assert.match(routeSource, /\/cancel/);
  assert.match(routeSource, /WORD_REVIEW_STREAM_TIMEOUT/);
  assert.match(routeSource, /history\.map/);
  assert.match(routeSource, /"failed", "timeout"/);
  assert.doesNotMatch(routeSource, /useFileUpload\(/);
  assert.doesNotMatch(routeSource, /usePublishedAgent\("qa-word-review"\)/);
  assert.doesNotMatch(routeSource, /fetchBrowserRuntimeConfig|word-translate/);
});
