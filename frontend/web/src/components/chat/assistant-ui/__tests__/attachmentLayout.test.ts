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

test("composer attachment chips share one height and reserve room for actions", () => {
  const css = read("../../../../styles/base.css");
  const card = css.match(/\.composer-attachment-card \{[^}]*\}/)?.[0] ?? "";
  assert.match(card, /height: 56px/);
  assert.match(card, /display: flex/);
  assert.doesNotMatch(css, /\.composer-attachment-remove \{[^}]*position: absolute/);
  assert.match(css, /\.composer-attachment-progress \{[^}]*position: absolute/);
  const chat = read("../../../../styles/chat.css");
  assert.match(chat, /\[data-more-left="true"\]::before,[\s\S]*?\[data-more-right="true"\]::after \{[^}]*pointer-events: none/);
  assert.doesNotMatch(chat, /\.composer-attachment-strip \{[^}]*mask-image/);
});
