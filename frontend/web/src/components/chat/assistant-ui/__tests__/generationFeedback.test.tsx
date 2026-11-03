import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { GenerationLoader } from "../elements/loading-state";

test("waiting feedback is one shimmering label without a dot grid", () => {
  const markup = renderToStaticMarkup(createElement(GenerationLoader, { label: "正在等待回复…" }));
  assert.match(markup, /role="status"/);
  assert.match(markup, /class="aui-shimmer">正在等待回复…</);
  assert.doesNotMatch(markup, /animate-pulse|grid-cols-3/);
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
