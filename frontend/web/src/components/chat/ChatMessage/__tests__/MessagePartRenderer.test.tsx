import assert from "node:assert/strict";
import test from "node:test";
import type { MessagePart } from "../../../../types";
import { createMessagePartRenderKeys } from "../MessagePartRenderer.tsx";

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
