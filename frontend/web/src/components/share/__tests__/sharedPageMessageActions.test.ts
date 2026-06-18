import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

test("shared page cannot re-enable feedback and share actions on chat messages", () => {
  const sharedPageSource = readFileSync(
    resolve(__dirname, "../SharedPage.tsx"),
    "utf8",
  );
  const chatMessageSource = readFileSync(
    resolve(__dirname, "../../chat/ChatMessage/index.tsx"),
    "utf8",
  );

  assert.doesNotMatch(sharedPageSource, /showFeedbackAndShareActions/);
  assert.doesNotMatch(chatMessageSource, /showFeedbackAndShareActions/);
  assert.doesNotMatch(chatMessageSource, /FeedbackButtons/);
  assert.doesNotMatch(chatMessageSource, /ShareButton/);
  assert.doesNotMatch(chatMessageSource, /ShareDialog/);
});
