import assert from "node:assert/strict";
import test from "node:test";
import { act, createElement } from "react";
import { MessagePrimitive, ThreadPrimitive, getExternalStoreMessages, useAuiState } from "@assistant-ui/react";
import { installSelectTestDom } from "../../../common/__tests__/selectTestDom";
import type { Message } from "../../../../types";
import { selectAssistantCopyText } from "../../../../types/assistantTextParts";
import { getAssistantUiDisplayParts, toAssistantUiMessage } from "../externalStoreRuntime";
import { createMessagePartRenderKeys } from "../../ChatMessage/MessagePartRenderer";

test("native parts keep one snapshot through streaming, classification, synchronization, cancellation and failure", async () => {
  const environment = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { AssistantUiMarkdownText, AssistantUiMessagePart, AssistantUiMessageStatus } = await import("../MessageParts");
  const { AssistantUiMessageActions } = await import("../MessageActions");
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  let boundMessage: Message | undefined;
  function Presentation() {
    const message = useAuiState((state) => getExternalStoreMessages<Message>(state.message)[0]);
    boundMessage = message;
    if (!message) return null;
    const parts = getAssistantUiDisplayParts(message);
    const keys = createMessagePartRenderKeys(message.id, parts);
    return <MessagePrimitive.Root>
      <div data-native-body>{parts.length ? parts.map((part, index) =>
        <AssistantUiMessagePart key={keys[index]} part={part} messageId={message.id} partIndex={index} isLast={index === parts.length - 1} />,
      ) : <MessagePrimitive.Parts components={{ Text: AssistantUiMarkdownText }} unstable_showEmptyOnNonTextEnd={false} />}</div>
      <AssistantUiMessageStatus />
      <AssistantUiMessageActions messageId={message.id} text={selectAssistantCopyText(message.parts, message.content)} />
    </MessagePrimitive.Root>;
  }
  const render = async (message: Message) => {
    await act(async () => root.render(createElement(AssistantUiTestProvider, { messages: [message], isRunning: Boolean(message.isStreaming) },
      createElement(ThreadPrimitive.Unstable_MessageById, { messageId: message.id, components: { Message: Presentation } }),
    )));
    assert.equal(boundMessage, message, "native content and business rendering must share the bound external snapshot");
  };
  const status = () => container.querySelector("[data-assistant-ui-stream-status]")?.getAttribute("data-assistant-ui-stream-status");
  const body = () => container.querySelector("[data-native-body]")!;
  let message: Message = { id: "native-message", role: "assistant", content: "", timestamp: new Date(0), isStreaming: true };
  try {
    await render(message);
    assert.equal(status(), "waiting");
    assert.equal(container.querySelectorAll('[role="status"]').length, 1);
    assert.equal(container.querySelector('[aria-label="复制"]'), null);

    const text = "# 标题\n\n" + "这是一段逐步显示的公开内容。".repeat(300);
    message = { ...message, content: text };
    await render(message);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });
    const partialLength = body().textContent!.length;
    assert.ok(partialLength > 0 && partialLength < text.length - 10, `expected animated prefix, got ${partialLength}`);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 700)); });
    assert.ok(body().querySelector("h1"));
    assert.equal(status(), "streaming");
    assert.ok(container.querySelector('[aria-label="复制"]'));

    message = { ...message, content: "预览", parts: [{ type: "text", logical_id: "part-preview", public_part_id: "part-preview", text_role: "pending", content: "预览" }] };
    await render(message);
    message = { ...message, content: "最终 **回答**", parts: [
      { type: "text", logical_id: "part-preview", public_part_id: "part-preview", text_role: "work", content: "已归入工作过程" },
      { type: "text", logical_id: "part-answer", public_part_id: "part-answer", text_role: "answer", content: "最终 **回答**" },
    ], isStreaming: false, isSynchronizing: true };
    await render(message);
    assert.equal(status(), "synchronizing");
    assert.ok(container.querySelector('[aria-label="复制"]'));
    assert.match(JSON.stringify(toAssistantUiMessage(message)), /已归入工作过程/);
    assert.equal(getAssistantUiDisplayParts(message).length, (toAssistantUiMessage(message).content as unknown[]).length);

    message = { ...message, isSynchronizing: false };
    await render(message);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 350)); });
    assert.equal(status(), undefined);
    assert.equal(body().querySelector("strong")?.textContent, "回答");
    assert.ok(container.querySelector('[aria-label="复制"]'));

    message = { ...message, cancelled: true, content: "保留已经收到的文字", parts: [{ type: "text", content: "保留已经收到的文字" }, { type: "cancelled" }] };
    await render(message);
    assert.match(body().textContent!, /保留已经收到的文字/);
    assert.match(body().textContent!, /生成已取消/);
    assert.equal(status(), undefined);

    message = { ...message, cancelled: false, parts: [{ type: "run_status", event_id: "failure", event_type: "run_failed", stage: "private-stage", severity: "error", message: "private-error-secret" }] };
    await render(message);
    assert.match(body().textContent!, /执行失败/);
    assert.ok(body().querySelector('[role="alert"]'));
    assert.doesNotMatch(container.textContent!, /private-stage|private-error-secret/);
    assert.doesNotMatch(JSON.stringify(toAssistantUiMessage(message)), /private-stage|private-error-secret/);

    window.matchMedia = (media) => ({ matches: media === "(prefers-reduced-motion: reduce)", media, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; } });
    message = { id: "reduced-motion", role: "assistant", content: text, timestamp: new Date(0), isStreaming: true };
    await render(message);
    assert.ok(body().textContent!.length >= text.length - 10, "reduced motion reveals the current text immediately");
  } finally {
    await act(async () => root.unmount());
    environment.close();
  }
});

test("native and explicit Markdown render CJK punctuation emphasis without rewriting source or code", async () => {
  const environment = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { AssistantUiMarkdownText } = await import("../MessageParts");
  const { MarkdownRenderer } = await import("../../ChatMessage/MarkdownRenderer");
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const labels = ["共享盘访问失败：", "查询结果超过工具输出上限：", "一次路径未找到：", "文件保存与附件的首次尝试未成功："];
  const content = labels.map((label) => `- **${label}**后续说明 \`reauth_required\``).join("\n")
    + "\n\n最终结果为 **0 个公式错误**。"
    + "\n\n~~旧方案：~~新方案；~~旧方案（待替换）~~后文。"
    + "\n\n`**代码：**保持原样` 与 \\*\\*转义：\\*\\*保持原样。"
    + "\n\n[安全参考](https://example.com/**reference**)";
  function Presentation() {
    return <MessagePrimitive.Root><div data-native-cjk>
      <MessagePrimitive.Parts components={{ Text: AssistantUiMarkdownText }} unstable_showEmptyOnNonTextEnd={false} />
    </div></MessagePrimitive.Root>;
  }
  const render = async (text: string, streaming = false) => {
    const message: Message = { id: "cjk-message", role: "assistant", content: text, timestamp: new Date(0), isStreaming: streaming };
    await act(async () => root.render(<>
      <AssistantUiTestProvider messages={[message]} isRunning={streaming}>
        <ThreadPrimitive.Unstable_MessageById messageId={message.id} components={{ Message: Presentation }} />
      </AssistantUiTestProvider>
      <div data-explicit-cjk><MarkdownRenderer content={text} /></div>
    </>));
  };
  try {
    window.matchMedia = (media) => ({ matches: media === "(prefers-reduced-motion: reduce)", media, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; } });
    await render("- **共享盘访问失败：*", true);
    await render(content, true);
    await render(content);
    for (const selector of ["[data-native-cjk]", "[data-explicit-cjk]"]) {
      const body = container.querySelector(selector)!;
      assert.deepEqual([...body.querySelectorAll("li strong")].map((node) => node.textContent), labels);
      assert.equal(body.querySelectorAll("strong").length, 5);
      assert.equal(body.querySelectorAll("li").length, 4);
      assert.deepEqual([...body.querySelectorAll("del")].map((node) => node.textContent), ["旧方案：", "旧方案（待替换）"]);
      assert.equal(body.querySelectorAll("code")[4]?.textContent, "**代码：**保持原样");
      assert.match(body.textContent!, /\*\*转义：\*\*保持原样/);
      assert.equal(body.querySelector("a")?.getAttribute("href"), "https://example.com/**reference**");
    }
    assert.equal(selectAssistantCopyText(undefined, content), content);
  } finally {
    await act(async () => root.unmount());
    environment.close();
  }
});

test("streaming emphasis is completed only for the active assistant display, never source or terminal text", async () => {
  const environment = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { AssistantUiMarkdownText } = await import("../MessageParts");
  const { AssistantUiMessageActions } = await import("../MessageActions");
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  let boundMessage: Message | undefined;
  function Presentation() {
    const message = useAuiState((state) => getExternalStoreMessages<Message>(state.message)[0]);
    boundMessage = message;
    return <MessagePrimitive.Root>
      <div data-streaming-markdown><MessagePrimitive.Parts components={{ Text: AssistantUiMarkdownText }} /></div>
      {message && !message.isStreaming && <AssistantUiMessageActions messageId={message.id} text={selectAssistantCopyText(message.parts, message.content)} />}
    </MessagePrimitive.Root>;
  }
  const render = async (content: string, options: Partial<Message> = {}) => {
    const message: Message = { id: "streaming-markdown", role: "assistant", content, timestamp: new Date(0), isStreaming: true, ...options };
    await act(async () => root.render(<AssistantUiTestProvider messages={[message]} isRunning={Boolean(message.isStreaming)}>
      <ThreadPrimitive.Unstable_MessageById messageId={message.id} components={{ Message: Presentation }} />
    </AssistantUiTestProvider>));
    assert.equal(boundMessage, message);
    assert.equal(boundMessage.content, content);
    assert.equal(selectAssistantCopyText(boundMessage.parts, boundMessage.content), content);
    return container.querySelector("[data-streaming-markdown]")!;
  };
  try {
    window.matchMedia = (media) => ({ matches: media === "(prefers-reduced-motion: reduce)", media, onchange: null, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; } });
    for (const content of ["**标题：", "**标题：*", "**标题：**后文"]) {
      const body = await render(content);
      assert.equal(body.querySelector("strong")?.textContent, "标题：");
      assert.doesNotMatch(body.textContent!, /\*/);
    }
    assert.equal((await render("*说明")).querySelector("em")?.textContent, "说明");
    assert.equal((await render("***重点")).querySelector("strong em, em strong")?.textContent, "重点");
    assert.equal((await render("~~旧值：")).querySelector("del")?.textContent, "旧值：");

    for (const content of ["`**代码：", String.raw`\*\*转义：\*\*后文`, "[参考](https://example.com/partial", "![图片](https://example.com/partial", "$x**", "<span"]) {
      const body = await render(content);
      assert.equal(Boolean(body.querySelector("strong")), false);
      assert.equal(Boolean(body.querySelector("img, a[href^='streamdown:']")), false);
      if (content.includes("https://example.com/partial")) {
        assert.equal(body.textContent, content);
        assert.equal(body.querySelector("a")?.getAttribute("href"), "https://example.com/partial");
      }
      if (content.startsWith("`")) assert.match(body.textContent!, /\*\*代码：/);
    }
    for (const options of [
      { isStreaming: false },
      { isStreaming: false, cancelled: true },
      { isSynchronizing: true },
      { isStreaming: false, parts: [{ type: "text" as const, content: "**未完成：" }, { type: "run_status" as const, event_id: "failed-markdown", event_type: "run_failed", stage: "terminal", severity: "error" as const, message: "" }] },
      { role: "user" as const },
    ]) {
      const body = await render("**未完成：", options);
      assert.equal(body.querySelector("strong"), null);
      assert.equal(body.textContent, "**未完成：");
    }

    const originalClipboard = Object.getOwnPropertyDescriptor(navigator, "clipboard");
    let copied = "";
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: async (text: string) => { copied = text; } } });
    try {
      await render("**复制原文：", { isStreaming: false });
      const copy = container.querySelector('[aria-label="复制"]') as HTMLButtonElement;
      assert.ok(copy);
      await act(async () => copy.click());
      assert.equal(copied, "**复制原文：");
    } finally {
      if (originalClipboard) Object.defineProperty(navigator, "clipboard", originalClipboard);
      else Reflect.deleteProperty(navigator, "clipboard");
    }
  } finally {
    await act(async () => root.unmount());
    environment.close();
  }
});

test("public tool keys survive sanitized object replacement", () => {
  const message: Message = { id: "tool-owner", role: "assistant", content: "", timestamp: new Date(0), parts: [{
    type: "tool", name: "private-name", args: { private: "ignored" }, public_operation_id: "operation-stable", public_category: "read", status: "started",
  }] };
  const before = createMessagePartRenderKeys(message.id, getAssistantUiDisplayParts(message));
  const after = createMessagePartRenderKeys(message.id, getAssistantUiDisplayParts({ ...message, parts: [{ ...message.parts![0], status: "completed" } as NonNullable<Message["parts"]>[number]] }));
  assert.deepEqual(after, before);
});

test("native markdown mermaid fences mount MermaidDiagram instead of CodeMirror", async () => {
  const environment = installSelectTestDom();
  const { createLobeTestRoot } = await import("../../../common/__tests__/lobeUiTestRoot");
  const { AssistantUiTestProvider } = await import("./assistantUiTestProvider");
  const { AssistantUiMarkdownText } = await import("../MessageParts");
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  const message: Message = {
    id: "mermaid-message",
    role: "assistant",
    content: "```mermaid\nflowchart TD\n  A[Start] --> B[End]\n```",
    timestamp: new Date(0),
  };
  function Presentation() {
    return (
      <MessagePrimitive.Root>
        <div data-mermaid-body>
          <MessagePrimitive.Parts
            components={{ Text: AssistantUiMarkdownText }}
            unstable_showEmptyOnNonTextEnd={false}
          />
        </div>
      </MessagePrimitive.Root>
    );
  }
  const originalGetBBox = Object.getOwnPropertyDescriptor(SVGElement.prototype, "getBBox");
  Object.defineProperty(SVGElement.prototype, "getBBox", {
    configurable: true,
    value: () => ({ x: 0, y: 0, width: 40, height: 20 }),
  });
  try {
    await act(async () => {
      root.render(
        createElement(
          AssistantUiTestProvider,
          { messages: [message], isRunning: false },
          createElement(ThreadPrimitive.Unstable_MessageById, {
            messageId: message.id,
            components: { Message: Presentation },
          }),
        ),
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 100));
    });
    assert.ok(container.querySelector(".mermaid-diagram"));
    assert.equal(container.querySelector(".cm-editor"), null);
  } finally {
    if (originalGetBBox) {
      Object.defineProperty(SVGElement.prototype, "getBBox", originalGetBBox);
    } else {
      delete (SVGElement.prototype as { getBBox?: unknown }).getBBox;
    }
    await act(async () => root.unmount());
    environment.close();
  }
});
