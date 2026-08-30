import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import type { Message } from "../../../../types/message.ts";
import type { SessionInputFile } from "../../../../services/api/session.ts";
import { buildAgentMarketWorkspacePath } from "../../../../features/agent-market/agentMarketSelection.ts";
import { getSessionRouteSyncAction } from "../useSessionSync.ts";
import { mergeProjectedSessionFiles } from "../sessionInputFiles.ts";

const agentProfile = {
  agent_id: "agent/support",
  expected_revision: 12,
};

const inputFile: SessionInputFile = {
  file_id: "file-report",
  run_id: "run-agent",
  name: "report.pdf",
  mime_type: "application/pdf",
  size_bytes: 2048,
  preview_url: "/api/ai/files/file-report/preview?session_id=agent-session",
  download_url: "/api/ai/files/file-report/download?session_id=agent-session",
};

test("keeps Agent workspace Chat routes and run-bound file affordances together", () => {
  const workspaceBasePath = buildAgentMarketWorkspacePath(agentProfile);
  assert.equal(
    buildAgentMarketWorkspacePath(agentProfile, "agent/session"),
    "/agent-market/agent%2Fsupport/12/chat/agent%2Fsession",
  );
  assert.deepEqual(
    getSessionRouteSyncAction({
      activeTab: "chat",
      pathname: workspaceBasePath,
      browserPathname: workspaceBasePath,
      sessionId: "agent-session-next",
      urlSessionId: undefined,
      externalNavigate: false,
      sessionRouteBasePath: workspaceBasePath,
    }),
    {
      type: "replace-url",
      path: `${workspaceBasePath}/agent-session-next`,
    },
  );

  const messages: Message[] = [
    {
      id: "agent-message",
      role: "user",
      runId: "run-agent",
      content: "review this report",
      timestamp: new Date(0),
    },
    {
      id: "other-message",
      role: "user",
      runId: "run-other",
      content: "unrelated",
      timestamp: new Date(1),
    },
  ];
  const hydrated = mergeProjectedSessionFiles(messages, [inputFile]);

  assert.deepEqual(hydrated[0].attachments, [
    {
      id: "file-report",
      key: "file-report",
      name: "report.pdf",
      type: "document",
      mimeType: "application/pdf",
      size: 2048,
      url: inputFile.preview_url,
      downloadUrl: inputFile.download_url,
    },
  ]);
  assert.equal(hydrated[1].attachments, undefined);

  const source = readFileSync(new URL("../ChatView.tsx", import.meta.url), "utf8");
  assert.match(source, /sessionApi[\s\S]*\.getInputFiles\(sessionId\)/);
  assert.match(source, /navigate\(`\$\{sessionRouteBasePath\}\/\$\{response\.session\.id\}`\)/);
  assert.match(
    source,
    /mergeProjectedSessionFiles\(\s*messages,\s*visibleWorkspaceProjection\.inputFiles,\s*\)/,
  );
});

test("connects the visible recovery projection to the existing reconnect action", () => {
  const source = readFileSync(new URL("../ChatView.tsx", import.meta.url), "utf8");
  const locale = JSON.parse(
    readFileSync(
      new URL("../../../../i18n/locales/zh.json", import.meta.url),
      "utf8",
    ),
  );

  assert.match(source, /<ChatConnectionStatus/);
  assert.match(source, /status=\{visibleConnectionStatus\}/);
  assert.match(source, /owner=\{activeConnectionOwner\}/);
  assert.match(source, /onReconnect=\{onReconnect\}/);
  assert.deepEqual(locale.chat.connectionStatus, {
    connecting: "正在连接任务更新…",
    disconnected: "实时更新已断开，请重新连接以继续接收任务进度。",
    reconnect: "重新连接",
    reconnecting: "连接中断，正在恢复任务更新…",
    reconnectingAction: "正在连接…",
    recovering_gap: "正在校准已接收内容和任务状态…",
  });
});
