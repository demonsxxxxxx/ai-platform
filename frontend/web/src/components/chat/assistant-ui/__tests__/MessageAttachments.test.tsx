import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { createLobeServerTestRoot } from "../../../common/__tests__/lobeUiTestRoot";
const renderToStaticMarkup = await createLobeServerTestRoot();
import { MessageAttachments } from "../MessageAttachments";
import type { MessageAttachment } from "../../../../types";

function attachment(url: string): MessageAttachment {
  return {
    id: "message-file-1",
    key: "authorized-key",
    name: "report.png",
    type: "image",
    mimeType: "image/png",
    size: 12,
    url,
  };
}

test("message attachments use the safe image resolver before rendering previews", () => {
  const markup = renderToStaticMarkup(
    React.createElement(MessageAttachments, {
      attachments: [
        attachment(
          "/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret%252Freport.png/download",
        ),
      ],
    }),
  );

  assert.match(markup, /data-slot="message-attachments"/);
  assert.doesNotMatch(markup, /<img\b/);
  assert.doesNotMatch(markup, /%252Eclaude|%252Fruns/i);
});

test("message attachments retain the official received-file slot for documents", () => {
  const documentAttachment: MessageAttachment = {
    ...attachment("/files/report.pdf"),
    id: "message-file-2",
    name: "report.pdf",
    type: "document",
    mimeType: "application/pdf",
  };
  const markup = renderToStaticMarkup(
    React.createElement(MessageAttachments, {
      attachments: [documentAttachment],
    }),
  );

  assert.match(markup, /data-slot="message-attachment"/);
  assert.match(markup, /report\.pdf/);
  assert.doesNotMatch(markup, /href=/);
});
