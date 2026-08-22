import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ThreadPrimitive } from "@assistant-ui/react";
import { AssistantUiProjection } from "../AssistantUiProjection";
import { AssistantUiMessageFrame } from "../MessageFrame";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("assistant-ui projection mounts a non-empty transcript without replacing the source messages", () => {
  const html = renderToStaticMarkup(
    createElement(
      AssistantUiProjection,
      {
        messages: [{
          id: "message-1",
          role: "assistant",
          content: "visible answer",
          timestamp: new Date("2026-01-01T00:00:00Z"),
          parts: [{ type: "text", content: "visible answer" }],
        }],
        isRunning: false,
        actions: {
          sendMessage: async () => undefined,
          cancel: async () => undefined,
          reconnect: async () => undefined,
          loadHistory: async () => undefined,
        },
        children: createElement(
          ThreadPrimitive.Viewport,
          null,
          createElement(
            ThreadPrimitive.Unstable_MessageById,
            {
              messageId: "message-1",
              components: {
                Message: () =>
                  createElement(
                    AssistantUiMessageFrame,
                    null,
                    createElement("div", { "data-transcript": true }, "visible answer"),
                  ),
              },
            },
          ),
        ),
      },
    ),
  );
  assert.match(html, /data-transcript/);
  assert.match(html, /visible answer/);
  assert.match(html, /role="group"/);
  assert.match(html, /tabindex="0"/);
  assert.match(html, /aria-label="Assistant message"/);
});
test("assistant-ui projection uses accessible primitive wrappers without replacing legacy layout owners", () => {
  const projection = readFileSync(join(root, "AssistantUiProjection.tsx"), "utf8");
  const frame = readFileSync(join(root, "MessageFrame.tsx"), "utf8");
  const runtime = readFileSync(join(root, "externalStoreRuntime.ts"), "utf8");
  assert.match(projection, /AssistantRuntimeProvider/);
  assert.match(projection, /ThreadPrimitive\.Root/);
  assert.match(projection, /data-assistant-ui-projection/);
  assert.match(projection, /aria-live="polite"/);
  assert.match(frame, /MessagePrimitive\.Root/);
  assert.match(frame, /role="group"/);
  assert.match(frame, /tabIndex=\{0\}/);
  assert.match(frame, /aria-label="Assistant message"/);
  assert.match(runtime, /onNew/);
  assert.match(runtime, /onCancel/);
  assert.match(runtime, /onRefetchThread/);
  assert.match(runtime, /onReload/);
  assert.match(runtime, /Artifact authorization and rendering stay in ChatMessage/);
});

test("chat view keeps current composer and virtualized transcript while delegating runtime actions", () => {
  const chatView = readFileSync(join(root, "../../layout/AppContent/ChatView.tsx"), "utf8");
  assert.match(chatView, /Unstable_MessageById/);
  assert.match(chatView, /messageId=\{message\.id\}/);
  assert.match(chatView, /<ChatInput/);
  assert.match(chatView, /<Virtuoso/);
  assert.match(chatView, /<AssistantUiProjection/);
  assert.match(chatView, /onReconnect/);
  assert.match(chatView, /onLoadHistory/);
});
