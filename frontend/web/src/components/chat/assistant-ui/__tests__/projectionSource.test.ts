import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

test("assistant-ui projection uses accessible primitive wrappers without replacing legacy layout owners", () => {
  const projection = readFileSync(join(root, "AssistantUiProjection.tsx"), "utf8");
  const frame = readFileSync(join(root, "MessageFrame.tsx"), "utf8");
  const runtime = readFileSync(join(root, "externalStoreRuntime.ts"), "utf8");
  assert.match(projection, /AssistantRuntimeProvider/);
  assert.match(projection, /ThreadPrimitive\.Root/);
  assert.match(projection, /data-assistant-ui-projection/);
  assert.match(frame, /MessagePrimitive\.Root/);
  assert.match(runtime, /onNew/);
  assert.match(runtime, /onCancel/);
  assert.match(runtime, /onRefetchThread/);
  assert.match(runtime, /Artifact authorization and rendering stay in ChatMessage/);
});

test("chat view keeps current composer and virtualized transcript while delegating runtime actions", () => {
  const chatView = readFileSync(join(root, "../../layout/AppContent/ChatView.tsx"), "utf8");
  assert.match(chatView, /<ChatInput/);
  assert.match(chatView, /<Virtuoso/);
  assert.match(chatView, /<AssistantUiProjection/);
  assert.match(chatView, /onReconnect/);
  assert.match(chatView, /onLoadHistory/);
});
