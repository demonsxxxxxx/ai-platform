import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { AttachmentCard } from "../AttachmentCard";
import type { MessageAttachment } from "../../../types";

function createImageAttachment(
  overrides: Partial<MessageAttachment> = {},
): MessageAttachment {
  return {
    id: overrides.id ?? "attachment-1",
    key: overrides.key ?? "attachment-key",
    name: overrides.name ?? "private.png",
    type: overrides.type ?? "image",
    mimeType: overrides.mimeType ?? "image/png",
    size: overrides.size ?? 42,
    url: overrides.url ?? "/api/ai/artifacts/image/download",
    uploadProgress: overrides.uploadProgress,
    isUploading: overrides.isUploading,
  };
}

test("AttachmentCard does not render unsafe image attachment URLs", () => {
  const unsafeUrl =
    "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret%252Fimage.png/download";
  const markup = renderToStaticMarkup(
    React.createElement(AttachmentCard, {
      attachment: createImageAttachment({ url: unsafeUrl }),
    }),
  );

  assert.doesNotMatch(markup, /<img\b/);
  assert.doesNotMatch(markup, /%252Eclaude|%252Fruns/i);
});

test("AttachmentCard does not server-render raw safe platform image attachment URLs", () => {
  const markup = renderToStaticMarkup(
    React.createElement(AttachmentCard, {
      attachment: createImageAttachment({
        url: "/api/ai/artifacts/safe-image/download",
      }),
    }),
  );

  assert.doesNotMatch(markup, /<img\b/);
  assert.doesNotMatch(markup, /\/api\/ai\/artifacts\/safe-image\/download/);
});
