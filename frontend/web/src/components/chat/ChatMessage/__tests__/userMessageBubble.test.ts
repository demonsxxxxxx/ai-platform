import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { getUserMessageActionButtonVisibilityClass } from "../userMessageBubbleState";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

test("keeps user message action buttons visible for the latest message", () => {
  const className = getUserMessageActionButtonVisibilityClass(true);

  assert.equal(className.includes("opacity-0"), false);
  assert.equal(className.includes("group-hover:opacity-100"), false);
});

test("hides older user message action buttons until hover", () => {
  const className = getUserMessageActionButtonVisibilityClass(false);

  assert.equal(className.includes("opacity-0"), true);
  assert.equal(className.includes("group-hover:opacity-100"), true);
});

test("uses AttachmentCard resolved blob src before opening user image previews", () => {
  const source = readSource("../UserMessageBubble.tsx");

  assert.match(source, /previewSrc/);
  assert.doesNotMatch(source, /resolveSafeAttachmentImageSrc/);
  assert.doesNotMatch(source, /getFullUrl\(attachment\.url\)/);
});

test("renders the locked Skill label separately from user-authored message content", () => {
  const source = readSource("../UserMessageBubble.tsx");

  assert.match(source, /data-locked-skill-label/);
  assert.match(source, /chat\.message\.lockedSkill/);
  assert.doesNotMatch(source, /`\/skill|"\/skill|'\/skill/);
});
