import assert from "node:assert/strict";
import test from "node:test";
import { act, createElement, useLayoutEffect } from "react";
// jsdom is the pinned mounted-test runtime and does not ship declarations here.
// @ts-expect-error jsdom runtime import.
import { JSDOM } from "jsdom";
import { clearAuthScopedCaches } from "../../../../services/api/authCacheInvalidation.ts";
import { AttachmentPreviewHost } from "../../AttachmentPreviewHost.tsx";
import { getAttachmentPreviewState, openAttachmentPreview } from "../../attachmentPreviewStore.ts";
import { claimChatPreviewSession } from "../../chatPreviewSession.ts";
import { BlockPreviewPortal } from "../items/McpBlockPreview.tsx";
import { getBlockPreview, openBlockPreview } from "../items/blockPreviewStore.ts";
import { getSidebarHistoryLength, goBackSidebar } from "../items/sidebarHistoryStore.ts";
import { renderToStaticMarkup } from "react-dom/server";
import type { MessagePart } from "../../../../types";
import { clearAllLoadingStates } from "../../../../hooks/useAgent/messageParts.ts";
import { PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS } from "../../../../hooks/useAgent/publicTerminalPresentation.ts";
import { getVisibleMessageParts } from "../messagePartVisibility.ts";
import {
  createMessagePartRenderKeys,
  MessagePartRenderer,
} from "../MessagePartRenderer.tsx";

test("keeps streaming text object identity stable without using mutable content as a key", () => {
  const streamingText = {
    type: "text",
    content: "first token",
  } as MessagePart;
  const firstKey = createMessagePartRenderKeys("message-a", [streamingText])[0];
  (streamingText as Extract<MessagePart, { type: "text" }>).content =
    "first token and second token";
  const secondKey = createMessagePartRenderKeys("message-a", [streamingText])[0];

  assert.equal(firstKey, secondKey);
  assert.doesNotMatch(secondKey, /first token|second token/);
});

test("renders public commentary inline instead of behind a work-details control", () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      isStreaming: true,
      part: {
        type: "summary",
        content: "正在检查授权输入。",
        isStreaming: true,
        summary_id: "summary-1",
      } satisfies MessagePart,
    }),
  );

  assert.match(markup, /data-message-commentary/);
  assert.match(markup, /正在检查授权输入/);
  assert.doesNotMatch(markup, /<button|aria-expanded/);
});

test("renders public tool metadata without raw arguments or results", () => {
  for (const [category, label, canonicalName] of [
    ["skill", "使用 Skill", "QA Review"],
    ["mcp", "调用 MCP 工具", "MCP"],
    ["read", "读取", "Read"],
    ["write", "写入", "Write"],
    ["edit", "编辑", "Edit"],
    ["search", "搜索", "Search"],
    ["execute", "执行", "Execute"],
  ] as const) {
    const markup = renderToStaticMarkup(
      createElement(MessagePartRenderer, {
        isLast: true,
        part: {
          type: "tool",
          name: `raw-label-${category} /workspace/private`,
          args: { command: "cat private-token", timeout: 60 },
          result: "private command output",
          public_category: category,
          public_display_name:
            category === "skill"
              ? "QA Review"
              : "ignored label /workspace/private",
          public_operation_id: `operation-${category}`,
          status: "completed",
          duration_ms: category === "skill" ? 0 : 1200,
        } satisfies MessagePart,
      }),
    );
    assert.match(markup, new RegExp(`>${label}：${canonicalName}<`));
    assert.match(markup, category === "skill" ? /0毫秒/ : /1\.20秒/);
    assert.doesNotMatch(
      markup,
      /raw-label|workspace|private-token|private command output|command/,
    );
  }

  for (const part of [
    {
      type: "tool",
      name: "Unknown operation",
      args: {},
      public_category: "future-private-category",
      public_operation_id: "operation-unknown",
      status: "completed",
    },
    {
      type: "tool",
      name: "Malformed identity",
      args: {},
      public_category: "read",
      public_operation_id: "../../private-operation",
      status: "completed",
    },
    {
      type: "tool",
      name: "Missing lifecycle",
      args: {},
      public_category: "read",
      public_operation_id: "operation-missing-status",
    },
  ] as MessagePart[]) {
    assert.equal(
      renderToStaticMarkup(
        createElement(MessagePartRenderer, { part, isLast: true }),
      ),
      "",
    );
  }
});

test("stops unresolved public tool presentation after terminal cleanup without changing protocol outcomes", () => {
  const cleanedParts = clearAllLoadingStates([
    {
      type: "tool",
      name: "Read private path",
      args: { path: "/workspace/private" },
      status: "started",
      public_operation_id: "operation-stopped",
      public_category: "read",
    },
    {
      type: "tool",
      name: "Read",
      args: {},
      status: "completed",
      success: true,
      isPending: true,
      public_operation_id: "operation-completed",
      public_category: "read",
    },
    {
      type: "tool",
      name: "Write",
      args: {},
      status: "failed",
      success: false,
      isPending: true,
      public_operation_id: "operation-failed",
      public_category: "write",
    },
  ]);

  assert.deepEqual(
    cleanedParts.map((part) =>
      part.type === "tool"
        ? {
            status: part.status,
            isPending: part.isPending,
            cancelled: part.cancelled,
            success: part.success,
          }
        : null,
    ),
    [
      { status: "started", isPending: false, cancelled: true, success: undefined },
      { status: "completed", isPending: false, cancelled: undefined, success: true },
      { status: "failed", isPending: false, cancelled: undefined, success: false },
    ],
  );

  const projected = getVisibleMessageParts(cleanedParts);
  assert.equal(projected[0]?.type, "tool");
  assert.equal(projected[0]?.type === "tool" ? projected[0].status : null, "started");
  assert.equal(projected[0]?.type === "tool" ? projected[0].isPending : null, false);
  assert.equal(projected[0]?.type === "tool" ? projected[0].cancelled : null, true);

  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: projected[0]!,
    }),
  );
  assert.match(markup, /操作已停止，结果尚未确认/);
  assert.doesNotMatch(markup, /操作已开始|animate-spin/);
  assert.doesNotMatch(markup, /workspace|private/);
});

test("renders public execution kind and status from the Chinese catalog instead of backend copy", async () => {
  const step: Extract<MessagePart, { type: "execution_step" }> = {
    type: "execution_step",
    sequence: 6,
    step_id: "step-prepare-report",
    kind: "processing",
    progress: { current: 4, total: 4 },
    status: "completed",
    safe_file_name: null,
  };
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part: step, isLast: true }),
  );

  assert.match(markup, /处理/);
  assert.match(markup, /已完成/);
  assert.match(markup, /role="status"/);
  assert.match(markup, /4\/4/);
  assert.match(markup, /data-public-execution-process/);
  assert.doesNotMatch(markup, /Backend says/);
  assert.doesNotMatch(markup, /rounded-lg|border-/);
  assert.doesNotMatch(markup, /tool|execute/i);

  const [startedKey] = createMessagePartRenderKeys("message-a", [
    {
      ...step,
      status: "running",
      progress: { current: 0, total: 4 },
    },
  ]);
  const [completedKey] = createMessagePartRenderKeys("message-a", [step]);
  assert.equal(startedKey, completedKey);
});

test("renders sandbox readiness duration from v4 execution timestamps", () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      withinWorkDetails: true,
      part: {
        type: "execution_process",
        elapsed_ms: 1_250,
        steps: [{
          type: "execution_step",
          sequence: 2,
          step_id: "phase_sandbox_preparation",
          kind: "processing",
          stage: "sandbox_preparation",
          progress: { current: 1, total: 1 },
          status: "completed",
          safe_file_name: null,
          started_at: "2026-09-15T01:00:00.000Z",
          completed_at: "2026-09-15T01:00:01.250Z",
        }],
      } satisfies Extract<MessagePart, { type: "execution_process" }>,
    }),
  );

  assert.match(markup, /沙箱已就绪/);
  assert.match(markup, /用时 1\.25秒/);
  assert.match(markup, /data-sandbox-ready-duration/);
  assert.doesNotMatch(markup, /<details/);
});

test("hides legacy sandbox identifiers while v4 execution keeps readiness visible", () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "sandbox",
        status: "ready",
        sandbox_id: "private-sandbox-id",
        error: "private sandbox error",
        ready_duration_ms: 850,
      } satisfies Extract<MessagePart, { type: "sandbox" }>,
    }),
  );

  assert.equal(markup, "");
});

test("renders only public subagent lifecycle fields", () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "subagent",
        agent_id: "subagent-public-1",
        public_operation_id: "subagent-public-1",
        agent_name: "cat /workspace/private --token secret",
        input: "cat /workspace/private --token secret",
        result: "private worker result",
        error: "private worker error",
        status: "complete",
        depth: 1,
        duration_ms: 1_200,
        progress_percent: 100,
        current_category: "read",
      } satisfies Extract<MessagePart, { type: "subagent" }>,
    }),
  );

  assert.match(markup, /Sub-agent/);
  assert.match(markup, /Completed/);
  assert.match(markup, /Category: read/);
  assert.match(markup, /Progress: 100%/);
  assert.match(markup, /Duration: 1\.2s/);
  assert.doesNotMatch(
    markup,
    /workspace|private|secret|worker result|worker error/,
  );

  const legacy = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "subagent",
        agent_id: "private-agent-id",
        agent_name: "private_worker",
        input: "private prompt",
        status: "running",
        depth: 1,
      } satisfies Extract<MessagePart, { type: "subagent" }>,
    }),
  );
  assert.equal(legacy, "");
});

test("does not render thinking parts", () => {
  const completed = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "thinking",
        content: "公开思考摘要",
        public_reasoning: true,
        isStreaming: false,
      } satisfies Extract<MessagePart, { type: "thinking" }>,
    }),
  );
  const streaming = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      isStreaming: true,
      part: {
        type: "thinking",
        content: "正在核对证据",
        public_reasoning: true,
        isStreaming: true,
      } satisfies Extract<MessagePart, { type: "thinking" }>,
    }),
  );

  assert.equal(completed, "");
  assert.equal(streaming, "");
});

test("does not render tool parts without authorized public metadata", () => {
  for (const name of [
    "Bash",
    "read_file",
    "edit_file",
    "write_file",
    "grep",
    "glob",
    "mcp__private__tool",
    "reveal_file",
    "reveal_project",
  ]) {
    const markup = renderToStaticMarkup(
      createElement(MessagePartRenderer, {
        isLast: true,
        part: {
          type: "tool",
          name,
          args: {
            command: "cat /workspace/private --token secret",
            path: "/workspace/private",
          },
          result: { output: "private command output" },
          success: true,
        } satisfies Extract<MessagePart, { type: "tool" }>,
      }),
    );
    assert.equal(markup, "", name);
  }
});

test("renders binary lifecycle as a status row without a progress bar", async () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      isLast: true,
      part: {
        type: "execution_step",
        sequence: 1,
        step_id: "step-binary",
        kind: "analysis",
        progress: { current: 0, total: 1 },
        status: "running",
        safe_file_name: null,
      } satisfies Extract<MessagePart, { type: "execution_step" }>,
    }),
  );
  assert.match(markup, /分析/);
  assert.match(markup, /进行中/);
  assert.doesNotMatch(markup, /role="progressbar"|0\/1|0%/);
  assert.doesNotMatch(markup, /Backend|backend-stage-copy|step-binary/);
});

test("renders run status from allowlisted event type instead of backend message and stage", async () => {
  const part: Extract<MessagePart, { type: "run_status" }> = {
    type: "run_status",
    event_id: "evt-run-started",
    event_type: "run_started",
    stage: "backend execution stage",
    message: "Backend says run started",
    severity: "info",
  };
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part, isLast: true }),
  );

  assert.match(markup, /执行已开始/);
  assert.match(markup, /进行中/);
  assert.doesNotMatch(markup, /Backend|backend execution stage/);

  const unknownMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: {
        ...part,
        event_type: "private:token-bearing-event",
        stage: "C:\\private\\runtime",
        message: "stdout contains a private identifier",
      },
      isLast: true,
    }),
  );
  assert.match(unknownMarkup, /执行状态更新/);
  assert.doesNotMatch(
    unknownMarkup,
    /private|token-bearing|stdout|C:\\private/i,
  );
});

test("renders every public terminal detail without exposing backend message or stage", () => {
  for (const [detailCode, definition] of Object.entries(
    PUBLIC_TERMINAL_PRESENTATION_DEFINITIONS,
  )) {
    const markup = renderToStaticMarkup(
      createElement(MessagePartRenderer, {
        part: {
          type: "run_status",
          event_id: `evt-${detailCode}`,
          event_type: detailCode,
          stage: "C:\\private\\runtime",
          message: "backend token-bearing private detail",
          severity: definition.severity,
        } satisfies Extract<MessagePart, { type: "run_status" }>,
        isLast: true,
      }),
    );

    assert.ok(markup.includes(definition.defaultEventLabel), detailCode);
    assert.ok(markup.includes(definition.defaultMessage), detailCode);
    assert.doesNotMatch(
      markup,
      /执行状态更新|backend|token-bearing|private|runtime/i,
      detailCode,
    );
  }
});

test("renders a validated reconciliation correlation ID without backend text", () => {
  const basePart = {
    type: "run_status",
    event_id: "evt-terminal-reconciliation",
    event_type: "terminal_reconciliation_failed",
    stage: "private repository stage",
    message: "backend exception with token",
    severity: "error",
  } satisfies Extract<MessagePart, { type: "run_status" }>;
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: { ...basePart, run_reference: "run-correlation-123" },
      isLast: true,
    }),
  );

  assert.match(markup, /任务编号：run-correlation-123/);
  assert.doesNotMatch(markup, /backend exception|token|repository stage/i);

  const invalidMarkup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: { ...basePart, run_reference: "<private-run>" },
      isLast: true,
    }),
  );
  assert.doesNotMatch(invalidMarkup, /private-run|任务编号：/i);
});

test("renders password-protected PDF guidance instead of a generic failure", () => {
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, {
      part: {
        type: "run_status",
        event_id: "evt-pdf-password",
        event_type: "context_file_pdf_password_required",
        stage: "private parser path",
        message: "PdfReadError at /runtime/private.pdf",
        severity: "error",
      } satisfies Extract<MessagePart, { type: "run_status" }>,
      isLast: true,
    }),
  );

  assert.match(markup, /PDF 文件需要密码/);
  assert.match(markup, /请先解除密码保护后重新上传/);
  assert.doesNotMatch(markup, /执行状态更新|PdfReadError|runtime|private/i);
});

test("renders a specific safe file-size failure instead of a generic failure", () => {
  const part: Extract<MessagePart, { type: "run_status" }> = {
    type: "run_status",
    event_id: "evt-file-too-large",
    event_type: "context_file_too_large",
    stage: "private storage stage",
    message: "private token-bearing backend detail",
    severity: "error",
  };
  const markup = renderToStaticMarkup(
    createElement(MessagePartRenderer, { part, isLast: true }),
  );

  assert.match(markup, /文件超过处理上限/);
  assert.match(markup, /文件超过 128 MB，或文件总量超过 256 MB/);
  assert.doesNotMatch(markup, /private|token-bearing|storage stage/);
});

async function withPreviewOwnerDom(run: (harness: {
  render: (ownerKey: string, attachment?: boolean) => Promise<void>;
  remount: () => Promise<void>;
  body: () => string;
  act: typeof import("react").act;
}) => Promise<void>) {
  const dom = new JSDOM("<!doctype html><div id='root'></div>", {
    url: "http://localhost/", pretendToBeVisual: true,
  });
  const values: Record<string, unknown> = {
    window: dom.window, document: dom.window.document,
    localStorage: dom.window.localStorage, sessionStorage: dom.window.sessionStorage,
    navigator: dom.window.navigator, HTMLElement: dom.window.HTMLElement,
    Element: dom.window.Element, Node: dom.window.Node, CustomEvent: dom.window.CustomEvent,
    ResizeObserver: class { observe() {} unobserve() {} disconnect() {} },
    requestAnimationFrame: (callback: FrameRequestCallback) => dom.window.setTimeout(() => callback(Date.now()), 0),
    cancelAnimationFrame: (handle: number) => dom.window.clearTimeout(handle),
    IS_REACT_ACT_ENVIRONMENT: true,
  };
  const previous = new Map(Object.keys(values).map((key) => [
    key, Object.getOwnPropertyDescriptor(globalThis, key),
  ]));
  for (const [key, value] of Object.entries(values)) {
    Object.defineProperty(globalThis, key, { configurable: true, writable: true, value });
  }
  dom.window.matchMedia = () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent: () => false,
    media: "", onchange: null,
  });
  const { createRoot } = await import("react-dom/client");
  let root = createRoot(dom.window.document.getElementById("root")!);
  function PreviewOwner({ ownerKey, attachment }: { ownerKey: string; attachment: boolean }) {
    useLayoutEffect(() => { claimChatPreviewSession(ownerKey); }, [ownerKey]);
    return createElement(attachment ? AttachmentPreviewHost : BlockPreviewPortal);
  }
  clearAuthScopedCaches();
  try {
    await run({
      async render(ownerKey, attachment = false) {
        await act(async () => { root.render(createElement(PreviewOwner, { ownerKey, attachment })); });
      },
      async remount() {
        await act(async () => { root.unmount(); });
        root = createRoot(dom.window.document.getElementById("root")!);
      },
      body: () => dom.window.document.body.textContent ?? "",
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

test("preview auth invalidation clears mounted text and captured sidebar history before a new owner remounts", async () => {
  await withPreviewOwnerDom(async ({ render, remount, body, act }) => {
    await render("owner-a:session-a");
    await act(async () => { openBlockPreview({ type: "text", text: "synthetic first preview" }); });
    await act(async () => { openBlockPreview({ type: "text", text: "synthetic owner A text" }); });
    assert.ok(getSidebarHistoryLength() > 0);
    assert.match(body(), /synthetic owner A text/);
    await act(async () => { clearAuthScopedCaches(); });
    assert.equal(getBlockPreview(), null);
    assert.equal(getSidebarHistoryLength(), 0);
    assert.equal(goBackSidebar(), false);
    assert.doesNotMatch(body(), /synthetic owner A text|synthetic first preview/);
    await remount();
    await render("owner-b:session-b");
    assert.doesNotMatch(body(), /synthetic owner A text|synthetic first preview/);
  });
});

test("preview auth invalidation removes attachment metadata from the mounted host and a same-user new login", async () => {
  await withPreviewOwnerDom(async ({ render, remount, body, act }) => {
    await render("owner-a:session-a", true);
    await act(async () => { openAttachmentPreview({
      id: "synthetic-file-a", key: "synthetic-file-a", name: "synthetic-owner-A-file.png",
      type: "image", mimeType: "image/png", size: 1,
      url: "data:image/png;base64,aGVsbG8=",
    }, "user-message"); });
    for (let attempt = 0; attempt < 50 && !body().includes("synthetic-owner-A-file.png"); attempt += 1) {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });
    }
    assert.match(body(), /synthetic-owner-A-file\.png/);
    await act(async () => { clearAuthScopedCaches(); });
    assert.equal(getAttachmentPreviewState(), null);
    assert.doesNotMatch(body(), /synthetic-owner-A-file\.png/);
    await remount();
    await render("owner-a:session-a", true);
    assert.doesNotMatch(body(), /synthetic-owner-A-file\.png/);
  });
});

test("preview same-session remount preserves active text and valid back navigation", async () => {
  await withPreviewOwnerDom(async ({ render, remount, body, act }) => {
    await render("owner-a:session-a");
    await act(async () => { openBlockPreview({ type: "text", text: "first same-owner preview" }); });
    await act(async () => { openBlockPreview({ type: "text", text: "second same-owner preview" }); });
    await remount();
    await render("owner-a:session-a");
    assert.match(body(), /second same-owner preview/);
    await act(async () => { assert.equal(goBackSidebar(), true); });
    assert.match(body(), /first same-owner preview/);
  });
});

test("preview session transition clears state even when the host is subscribing for the first time", async () => {
  await withPreviewOwnerDom(async ({ render, remount, body, act }) => {
    claimChatPreviewSession("owner-a:session-a");
    openBlockPreview({ type: "text", text: "previous session preview" });
    await render("owner-a:session-b");
    assert.equal(getBlockPreview(), null);
    assert.doesNotMatch(body(), /previous session preview/);
    await act(async () => { openBlockPreview({ type: "text", text: "new session preview" }); });
    await remount();
    await render("owner-a:session-b");
    assert.match(body(), /new session preview/);
    assert.equal(goBackSidebar(), false);
  });
});

test("preview session transition clears attachment state without restoring an old host snapshot", async () => {
  await withPreviewOwnerDom(async ({ render, body }) => {
    claimChatPreviewSession("owner-a:session-a");
    openAttachmentPreview({ id: "old", key: "old", name: "previous-session.png", type: "image" }, "user-message");
    await render("owner-a:session-b", true);
    assert.equal(getAttachmentPreviewState(), null);
    assert.doesNotMatch(body(), /previous-session\.png/);
  });
});

test("departing preview owner cleanup cannot close a newer session's preview", async () => {
  await withPreviewOwnerDom(async ({ render, body, act }) => {
    const { createRoot } = await import("react-dom/client");
    const oldContainer = document.createElement("div");
    document.body.append(oldContainer);
    const oldRoot = createRoot(oldContainer);
    function DepartingOwner() {
      useLayoutEffect(() => { claimChatPreviewSession("old-owner:old-session"); }, []);
      return null;
    }
    await act(async () => { oldRoot.render(createElement(DepartingOwner)); });
    await render("new-owner:new-session");
    await act(async () => { openBlockPreview({ type: "text", text: "new owner's valid preview" }); });
    await act(async () => { oldRoot.unmount(); });
    assert.match(body(), /new owner's valid preview/);
    assert.equal(getBlockPreview()?.text, "new owner's valid preview");
    oldContainer.remove();
  });
});
