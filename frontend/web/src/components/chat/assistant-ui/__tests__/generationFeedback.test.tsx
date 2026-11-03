import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { GenerationLoader } from "../elements/loading-state";
import { getPublicToolActivityLabel } from "../../ChatMessage/messagePartVisibility";

test("waiting feedback names the current action and elapsed time without a dot grid", () => {
  const markup = renderToStaticMarkup(createElement(GenerationLoader, { label: "正在读取", elapsed: "4 秒" }));
  assert.match(markup, /role="status"/);
  assert.match(markup, /aui-thinking-dot/);
  assert.match(markup, /class="aui-shimmer aui-thinking-label[^"]*">正在读取</);
  assert.match(markup, /data-generation-elapsed="true">4 秒</);
  assert.doesNotMatch(markup, /animate-pulse|grid-cols-3/);
});

test("the waiting action uses the public category verb, never a raw tool name", () => {
  assert.equal(getPublicToolActivityLabel("read", "Read"), "读取");
  assert.equal(getPublicToolActivityLabel("skill", "合同审阅"), "使用 Skill：合同审阅");
  assert.equal(getPublicToolActivityLabel(undefined, "Read"), null);
});

test("stopped and failed turns use light states instead of bordered cards", () => {
  const renderer = readFileSync(new URL("../../ChatMessage/MessagePartRenderer.tsx", import.meta.url), "utf8");
  assert.match(renderer, /data-message-stopped/);
  assert.doesNotMatch(renderer, /border-\[var\(--theme-warning-ring\)\]/);
  assert.match(renderer, /"rounded-2xl bg-\[var\(--theme-danger-soft\)\] px-4 py-3/);
  const css = readFileSync(new URL("../../../../styles/chat.css", import.meta.url), "utf8");
  assert.match(css, /\.markdown-stopped > :last-child[^{]*::after/);
});

test("a sent message is anchored to the top instead of following to the bottom", () => {
  const hook = readFileSync(new URL("../../../layout/AppContent/useMessageScroll.hook.ts", import.meta.url), "utf8");
  assert.match(hook, /isLocalSendAppend\(previousMessagesRef\.current, latestMessagesRef\.current, isLoadingHistoryRef\.current\)/);
  assert.match(hook, /if \(sent && isLocalSendAppend\(previousMessages, messages, isLoadingHistory\)\) anchorSentMessage\(userIndex, sent\.id\)/);
  assert.match(hook, /request-scroll-to-bottom" && !sendAnchorActiveRef\.current/);
  const view = readFileSync(new URL("../../../layout/AppContent/ChatView.tsx", import.meta.url), "utf8");
  assert.match(view, /followOutput=\{followOutput\}/);
  assert.match(view, /index === anchoredReplyIndex \? "chat-anchor-reply"/);
});

test("streaming status is announced only; the caret is the visible cue", () => {
  const markup = renderToStaticMarkup(createElement(GenerationLoader, { label: "生成中", visuallyHidden: true }));
  assert.match(markup, /class="sr-only"/);
  assert.doesNotMatch(markup, /aui-shimmer/);
  const css = readFileSync(new URL("../../../../styles/chat.css", import.meta.url), "utf8");
  assert.match(css, /\.markdown-streaming > :last-child[^{]*::after/);
  assert.match(css, /prefers-reduced-motion: reduce\)[\s\S]*\.aui-shimmer \{[^}]*animation: none/);
});

test("tool rows keep status color on the icon instead of a tinted background", () => {
  const source = readFileSync(new URL("../../ChatMessage/elements/ToolCall.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(source, /bg-\[var\(--theme-(?:success|warning|danger)-soft\)\]/);
});

test("temporary send blocks never reuse the permission lock or text", () => {
  const input = readFileSync(new URL("../../ChatInput.tsx", import.meta.url), "utf8");
  const view = readFileSync(new URL("../../../layout/AppContent/ChatView.tsx", import.meta.url), "utf8");
  assert.match(input, /showPermissionLock=\{!canSend && sendBlockedReason === "permission"\}/);
  assert.match(input, /synchronizing: "chat\.sendBlockedSynchronizing"/);
  assert.match(view, /terminalResultSynchronizing\s*\?\s*"synchronizing"/);
});

test("only a local send is anchored; a restored history turn keeps the bottom scroll", async () => {
  const { isLocalSendAppend } = await import("../../../layout/AppContent/useMessageScroll.hook");
  const user = { role: "user" as const };
  const streaming = { role: "assistant" as const, isStreaming: true };
  const settled = { role: "assistant" as const, isStreaming: false };
  assert.equal(isLocalSendAppend([], [user, streaming], false), true);
  assert.equal(isLocalSendAppend([user, settled], [user, settled, user], false), true);
  assert.equal(isLocalSendAppend([], [user, settled], false), false, "history turn");
  assert.equal(isLocalSendAppend([], [user, streaming], true), false, "while history loads");
});
