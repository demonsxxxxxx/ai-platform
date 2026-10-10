import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { useInputHistory } from "../../../../hooks/useInputHistory.ts";
import { clearAuthScopedCaches } from "../../../../services/api/authCacheInvalidation.ts";

import type { Message } from "../../../../types/message.ts";
import type { SessionInputFile } from "../../../../services/api/session.ts";
import { buildAgentMarketWorkspacePath } from "../../../../features/agent-market/agentMarketSelection.ts";
import { getSessionRouteSyncAction } from "../useSessionSync.ts";
import { mergeProjectedSessionFiles } from "../sessionInputFiles.ts";

const agentProfile = {
  agent_id: "agent/support",
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
    "/agent-market/agent%2Fsupport/chat/agent%2Fsession",
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
  assert.doesNotMatch(source, /forkMessage|sessionRouteBasePath/);
  assert.match(
    source,
    /mergeProjectedSessionFiles\(\s*messages,\s*visibleWorkspaceProjection\.inputFiles,\s*\)/,
  );
  assert.match(source, /sessionApi\.stageProfileDriveFile\(reference\)/);
  assert.match(source, /isUploading:\s*true/);
  assert.doesNotMatch(source, /if \(!sessionId\) return;/);
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

test("keeps active conversations wide, readable, and visually compact", () => {
  const view = readFileSync(new URL("../ChatView.tsx", import.meta.url), "utf8");
  const message = readFileSync(
    new URL("../../../chat/ChatMessage/index.tsx", import.meta.url),
    "utf8",
  );
  const userMessage = readFileSync(
    new URL("../../../chat/ChatMessage/UserMessageBubble.tsx", import.meta.url),
    "utf8",
  );

  assert.match(view, /className="mx-auto max-w-\[68rem\] px-2"/);
  assert.match(message, /max-w-\[68rem\]/);
  assert.match(userMessage, /max-w-\[68rem\]/);
  assert.match(userMessage, /sm:max-w-\[75%\]/);
});

// Exercise the real hook while mounted: storage isolation alone is insufficient
// because a composer can retain loaded history through an identity transition.
type HistoryOwner = { id: string; tenant_id?: string } | null;

async function withInputHistory(
  run: (harness: {
    render: (owner: HistoryOwner, mountKey?: string) => Promise<void>;
    current: () => ReturnType<typeof useInputHistory>;
    storage: Storage;
    act: typeof import("react").act;
  }) => Promise<void>,
) {
  const dom = new JSDOM("<div id='root'></div>", { url: "http://localhost/" });
  const values = {
    window: dom.window,
    document: dom.window.document,
    localStorage: dom.window.localStorage,
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previous = new Map(Object.keys(values).map((key) => [
    key, Object.getOwnPropertyDescriptor(globalThis, key),
  ]));
  for (const [key, value] of Object.entries(values)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  const { act, createElement } = await import("react");
  const { createRoot } = await import("react-dom/client");
  const root = createRoot(dom.window.document.getElementById("root")!);
  let current!: ReturnType<typeof useInputHistory>;
  function Probe({ owner }: { owner: HistoryOwner }) {
    current = useInputHistory(owner);
    return createElement("div", null, current.history.join("|"));
  }
  clearAuthScopedCaches();
  try {
    await run({
      render: async (owner, key = "composer") => {
        await act(async () => { root.render(createElement(Probe, { owner, key })); });
      },
      current: () => current,
      storage: dom.window.localStorage,
      act,
    });
  } finally {
    await act(async () => { root.unmount(); });
    clearAuthScopedCaches();
    for (const [key, descriptor] of previous) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else Reflect.deleteProperty(globalThis, key);
    }
    dom.window.close();
  }
}

const historyOwnerA = { id: "history-user-a", tenant_id: "history-tenant-a" };
const historyOwnerB = { id: "history-user-b", tenant_id: "history-tenant-a" };

test("input history discards unowned legacy prompts instead of assigning them to a login", async () => {
  await withInputHistory(async ({ render, current, storage }) => {
    storage.setItem("chatInputHistory", JSON.stringify(["synthetic previous owner's prompt"]));
    await render(historyOwnerB);
    assert.deepEqual(current().history, []);
    assert.equal(current().navigateUp(""), null);
    assert.equal(storage.getItem("chatInputHistory"), null);
  });
});

test("input history changes owner and tenant without exposing loaded prompts or drafts", async () => {
  await withInputHistory(async ({ render, current, act }) => {
    await render(historyOwnerA);
    await act(async () => { current().pushHistory("synthetic A prompt"); });
    assert.equal(current().navigateUp("synthetic A unsent draft"), "synthetic A prompt");
    const stale = current();
    await render(historyOwnerB);
    assert.deepEqual(current().history, []);
    assert.equal(current().navigateDown(), null);
    assert.equal(current().navigateUp(""), null);
    await act(async () => { stale.pushHistory("late old-owner submission"); });
    assert.equal(stale.navigateUp(""), null);
    assert.deepEqual(current().history, []);
    await render(historyOwnerA);
    assert.deepEqual(current().history, ["synthetic A prompt"]);
    await act(async () => { stale.pushHistory("late submission after A to B to A"); });
    assert.equal(stale.navigateUp(""), null);
    assert.deepEqual(current().history, ["synthetic A prompt"]);
    await render({ ...historyOwnerA, tenant_id: "history-tenant-b" });
    assert.deepEqual(current().history, []);
  });
});

test("input history clears mounted and remounted state on logout and same-user login", async () => {
  await withInputHistory(async ({ render, current, storage, act }) => {
    await render(historyOwnerA);
    await act(async () => { current().pushHistory("synthetic A prompt"); });
    current().navigateUp("synthetic A draft");
    const stale = current();
    await act(async () => {
      clearAuthScopedCaches();
      // Invalidation must fence event handlers synchronously, before rerender.
      assert.equal(stale.navigateUp(""), null);
      assert.equal(stale.navigateDown(), null);
      stale.pushHistory("late submission after logout");
    });
    assert.deepEqual(current().history, []);
    assert.equal(current().navigateDown(), null);
    assert.equal(storage.length, 0);
    await render(null);
    await render(historyOwnerB, "new-owner");
    assert.deepEqual(current().history, []);
    await render(historyOwnerA, "same-user-new-login");
    assert.deepEqual(current().history, []);
    await act(async () => { current().pushHistory("fresh login prompt"); });
    assert.deepEqual(current().history, ["fresh login prompt"]);
  });
});

test("input history preserves same-owner remount and navigation, and bounds stored entries", async () => {
  await withInputHistory(async ({ render, current, storage, act }) => {
    await render(historyOwnerA);
    await act(async () => {
      for (let index = 0; index < 205; index += 1) current().pushHistory(`prompt ${index}`);
      current().pushHistory("   ");
    });
    assert.equal(current().history.length, 200);
    assert.equal(current().history[0], "prompt 5");
    await render(historyOwnerA, "remounted-composer");
    assert.equal(current().navigateUp("unsent draft"), "prompt 204");
    assert.equal(current().navigateUp("prompt 204"), "prompt 203");
    assert.equal(current().navigateDown(), "prompt 204");
    assert.equal(current().navigateDown(), "unsent draft");
    assert.equal(storage.getItem("chatInputHistory"), null);
    assert.equal(storage.length, 1);
  });
});

test("input history does not persist or recall without a stable authenticated owner", async () => {
  await withInputHistory(async ({ render, current, storage, act }) => {
    for (const owner of [null, { id: "missing-tenant" }, { id: "", tenant_id: "tenant" }]) {
      await render(owner);
      await act(async () => { current().pushHistory("unowned prompt"); });
      assert.deepEqual(current().history, []);
      assert.equal(current().navigateUp(""), null);
      assert.equal(storage.length, 0);
    }
  });
});


test("input history validates persisted entries and reads only the exact stable owner key", async () => {
  await withInputHistory(async ({ render, current, storage }) => {
    const owner = { id: "persisted-user", tenant_id: "persisted-tenant" };
    storage.setItem(`chatInputHistory:v2:${JSON.stringify([owner.tenant_id, owner.id])}`,
      JSON.stringify(["saved prompt", null, 17, { unexpected: true }]));
    storage.setItem('chatInputHistory:v2:["another-tenant","persisted-user"]',
      JSON.stringify(["other tenant prompt"]));
    await render(owner);
    assert.deepEqual(current().history, ["saved prompt"]);
    assert.equal(current().navigateUp("draft"), "saved prompt");
  });
});
