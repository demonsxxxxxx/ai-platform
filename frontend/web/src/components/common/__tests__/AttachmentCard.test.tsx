import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { createLobeServerTestRoot } from "./lobeUiTestRoot";
const renderToStaticMarkup = await createLobeServerTestRoot();

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
    uploadStatus: overrides.uploadStatus,
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

test("AttachmentCard exposes queued and capacity-waiting upload states", () => {
  const queued = renderToStaticMarkup(
    React.createElement(AttachmentCard, {
      attachment: createImageAttachment({
        isUploading: true,
        uploadStatus: "queued",
      }),
      isUploading: true,
    }),
  );
  const retrying = renderToStaticMarkup(
    React.createElement(AttachmentCard, {
      attachment: createImageAttachment({
        isUploading: true,
        uploadStatus: "retrying",
      }),
      isUploading: true,
    }),
  );

  assert.match(queued, /等待上传/);
  assert.match(retrying, /等待可用上传名额/);
  assert.doesNotMatch(queued, /role="progressbar"/);
  assert.doesNotMatch(retrying, /role="progressbar"/);
});

test("compact composer files expose full long names and visible remove actions without changing upload state", () => {
  const name = "ECM SDK说明文档v5.17_企业集成开发指南与接口参考_最终修订版.pdf";
  const markup = renderToStaticMarkup(React.createElement(AttachmentCard, {
    attachment: createImageAttachment({ name, type: "document", mimeType: "application/pdf", size: 1992294 }),
    variant: "editable", size: "compact", onRemove: () => {}, onClick: () => {},
  }));
  assert.match(markup, /composer-attachment-card/);
  assert.ok(markup.includes(`aria-label="预览 ${name}"`));
  assert.match(markup, /data-attachment-filename[^>]*>.*<span class="shrink-0">\.pdf/);
  assert.match(markup, /1.9 MB/);
  assert.doesNotMatch(markup, /sm:opacity-0|theme-info/);
  assert.ok(markup.includes(`aria-label="移除附件 ${name}"`));
});

for (const isUploading of [false, true]) {
  test(`editable attachment ${isUploading ? "cancel" : "remove"} action keeps a 24px target`, () => {
    const markup = renderToStaticMarkup(React.createElement(AttachmentCard, {
      attachment: createImageAttachment({ isUploading }),
      variant: "editable",
      size: "compact",
      isUploading,
      onRemove: () => {},
      onCancel: () => {},
    }));
    const label = isUploading ? "取消上传 private.png" : "移除附件 private.png";
    const button = markup.match(new RegExp(`<button[^>]*aria-label="${label}"[^>]*>`))?.[0];
    assert.ok(button, `missing ${label} action`);
    assert.match(button, /height:24px/);
    assert.match(button, /width:24px/);
    assert.match(button, /min-width:24px/);
    assert.match(button, /type="button"/);
  });
}

test("100% transferred still cannot preview until the upload is confirmed", () => {
  const markup = renderToStaticMarkup(React.createElement(AttachmentCard, {
    attachment: createImageAttachment({ uploadProgress: 100, uploadStatus: "uploading" }),
    isUploading: true, size: "compact", variant: "editable", onClick: () => {}, onCancel: () => {},
  }));
  assert.match(markup, /data-state="confirming"/);
  assert.match(markup, /<button(?=[^>]*data-slot="attachment-tile")(?=[^>]*\bdisabled="")/);
  assert.match(markup, /正在确认上传/);
  assert.doesNotMatch(markup, /role="progressbar"/);
});
