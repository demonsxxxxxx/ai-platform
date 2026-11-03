import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");

test("sent attachments sit above the text bubble, not inside it", () => {
  const bubble = read("../../ChatMessage/UserMessageBubble.tsx");
  const attachmentsAt = bubble.indexOf("{hasAttachments ? renderAttachments() : null}");
  const contentAt = bubble.indexOf("<UserMessageContent");
  assert.ok(attachmentsAt > 0 && attachmentsAt < contentAt, "attachments render before, outside the bubble");
  assert.doesNotMatch(bubble.slice(contentAt), /renderAttachments\(\)/);
  assert.match(read("../MessageAttachments.tsx"), /flex max-w-full flex-wrap justify-end gap-2/);
});

test("composer attachment chips share one height with corner remove badges", () => {
  const css = read("../../../../styles/base.css");
  const card = css.match(/\.composer-attachment-card \{[^}]*\}/)?.[0] ?? "";
  assert.match(card, /height: 56px/);
  assert.match(css, /\.composer-attachment-remove \{[^}]*position: absolute/);
  assert.match(css, /\.composer-attachment-progress \{[^}]*position: absolute/);
  assert.match(read("../../../../styles/chat.css"), /\.composer-attachment-strip \{[^}]*mask-image/);
});
