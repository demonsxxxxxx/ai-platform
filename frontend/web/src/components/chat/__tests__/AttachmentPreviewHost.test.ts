import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("attachment preview host is mounted at ChatView level", () => {
  const chatViewSource = readFileSync(
    new URL("../../layout/AppContent/ChatView.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    chatViewSource,
    /<AttachmentPreviewHost\s*\/>/,
    "ChatView should mount a global attachment preview host outside ChatInput",
  );
});

test("attachment preview host fills the mobile viewport", () => {
  const attachmentPreviewHostSource = readFileSync(
    new URL("../AttachmentPreviewHost.tsx", import.meta.url),
    "utf8",
  );

  assert.match(
    attachmentPreviewHostSource,
    /<LazyDocumentPreview[\s\S]*?\bmobileFillViewport\b[\s\S]*?\/>/,
    "Uploaded attachment previews should use the full mobile viewport",
  );
});

test("attachment preview host image previews use authenticated blob image hook", () => {
  const attachmentPreviewHostSource = readFileSync(
    new URL("../AttachmentPreviewHost.tsx", import.meta.url),
    "utf8",
  );

  assert.match(attachmentPreviewHostSource, /useSafeAttachmentImageSrc/);
  assert.match(attachmentPreviewHostSource, /safeImageUrl/);
  assert.doesNotMatch(
    attachmentPreviewHostSource,
    /imageUrl=\{\s*attachment\.type === "image"\s*\?\s*getFullUrl\(attachment\.url\)/,
  );
});

test("chat input attachment image viewer uses AttachmentCard resolved blob src", () => {
  const chatInputAttachmentsSource = readFileSync(
    new URL("../ChatInputAttachments.tsx", import.meta.url),
    "utf8",
  );

  assert.match(chatInputAttachmentsSource, /previewSrc/);
  assert.doesNotMatch(
    chatInputAttachmentsSource,
    /resolveSafeAttachmentImageSrc/,
  );
  assert.doesNotMatch(
    chatInputAttachmentsSource,
    /onImageViewerOpen\(getFullUrl\(attachment\.url\)/,
  );
});
