import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MarkdownContent } from "../MarkdownContent.tsx";
import { collectSessionImageGalleryItems } from "../sessionImageGallery.tsx";
import type { Message } from "../../../../types";

function createMessage(overrides: Partial<Message>): Message {
  return {
    id: overrides.id ?? "message-1",
    role: overrides.role ?? "assistant",
    content: overrides.content ?? "",
    timestamp: overrides.timestamp ?? new Date("2026-05-17T00:00:00.000Z"),
    ...overrides,
  };
}

function encodeRepeated(value: string, times: number): string {
  let encoded = value;
  for (let attempt = 0; attempt < times; attempt += 1) {
    encoded = encodeURIComponent(encoded);
  }
  return encoded;
}

test("collects session images from attachments and markdown in message order", () => {
  const messages: Message[] = [
    createMessage({
      id: "user-1",
      role: "user",
      content: "look ![inline](/inline-user.png)",
      attachments: [
        {
          id: "attachment-image",
          key: "uploads/attachment.png",
          name: "attachment.png",
          type: "image",
          mimeType: "image/png",
          size: 12,
          url: "/api/upload/file/attachment.png",
        },
        {
          id: "attachment-pdf",
          key: "uploads/file.pdf",
          name: "file.pdf",
          type: "document",
          mimeType: "application/pdf",
          size: 34,
          url: "/file.pdf",
        },
      ],
    }),
    createMessage({
      id: "assistant-1",
      role: "assistant",
      content: "",
      parts: [
        {
          type: "text",
          content: "rendered ![chart](/api/ai/artifacts/chart/download)",
        },
        {
          type: "tool",
          name: "reveal_file",
          success: true,
          args: { path: "/tmp/generated.png" },
          result: JSON.stringify({
            key: "revealed/generated.png",
            url: "/api/ai/artifacts/generated/download",
            name: "generated.png",
            type: "image",
            mimeType: "image/png",
            size: 56,
            _meta: { path: "/tmp/generated.png" },
          }),
        },
      ],
    }),
  ];

  const items = collectSessionImageGalleryItems(messages);

  assert.deepEqual(
    items.map((item) => [item.id, item.src, item.alt, item.group]),
    [
      [
        "user-1:attachment:attachment-image",
        "/api/upload/file/attachment.png",
        "attachment.png",
        "conversation",
      ],
      [
        "assistant-1:part:0:image:0",
        "/api/ai/artifacts/chart/download",
        "chart",
        "conversation",
      ],
    ],
  );

  assert.equal(items.filter((item) => item.group === "conversation").length, 2);
});

test("ignores legacy reveal tool payloads in the session gallery", () => {
  const messages: Message[] = [
    createMessage({
      id: "assistant-1",
      role: "assistant",
      parts: [
        {
          type: "tool",
          name: "reveal_file",
          success: true,
          args: { path: "/workspace/api-image.png" },
          result: JSON.stringify({
            url: "/api/ai/artifacts/api-image/download",
            name: "api-image.png",
            mimeType: "image/png",
          }),
        },
      ],
    }),
  ];

  assert.deepEqual(collectSessionImageGalleryItems(messages), []);
});

test("ignores raw subagent payload images and keeps public child content", () => {
  const messages: Message[] = [
    createMessage({
      id: "assistant-subagent",
      role: "assistant",
      parts: [
        {
          type: "subagent",
          agent_id: "subagent-public-1",
          public_operation_id: "subagent-public-1",
          agent_name: "Verification worker",
          input: "![private input](/api/ai/artifacts/private-input/download)",
          result: "![private result](/api/ai/artifacts/private-result/download)",
          status: "complete",
          depth: 1,
          parts: [
            {
              type: "summary",
              summary_id: "summary-public-1",
              content: "![public child](/api/ai/artifacts/public-child/download)",
            },
          ],
        },
        {
          type: "subagent",
          agent_id: "legacy-private-agent",
          agent_name: "private_worker",
          input: "private prompt",
          status: "complete",
          depth: 1,
          parts: [
            {
              type: "text",
              content: "![legacy child](/api/ai/artifacts/legacy-child/download)",
            },
          ],
        },
      ],
    }),
  ];

  const items = collectSessionImageGalleryItems(messages);
  assert.deepEqual(items.map((item) => item.src), [
    "/api/ai/artifacts/public-child/download",
  ]);
  assert.doesNotMatch(JSON.stringify(items), /private-input|private-result|legacy-child/);
});

test("filters unsafe markdown, html, and attachment image urls from the session gallery", () => {
  const encodedSettingsUrl = encodeRepeated(".claude/settings.png", 5);
  const messages: Message[] = [
    createMessage({
      id: "user-unsafe",
      role: "user",
      content: [
        "unsafe ![http](http://cdn.example.com/http.png)",
        `unsafe ![settings](/api/ai/artifacts/${encodedSettingsUrl}/download)`,
        '<img src="http://cdn.example.com/html.png">',
        '<img src="/api/ai/artifacts/safe-image/download">',
      ].join("\n"),
      attachments: [
        {
          id: "unsafe-http",
          key: "uploads/http.png",
          name: "http.png",
          type: "image",
          mimeType: "image/png",
          size: 12,
          url: "http://cdn.example.com/attachment.png",
        },
        {
          id: "unsafe-internal",
          key: "uploads/internal.png",
          name: "internal.png",
          type: "image",
          mimeType: "image/png",
          size: 12,
          url: `/api/ai/artifacts/${encodedSettingsUrl}/download`,
        },
        {
          id: "safe-api",
          key: "uploads/safe.png",
          name: "safe.png",
          type: "image",
          mimeType: "image/png",
          size: 12,
          url: "/api/ai/artifacts/safe-attachment/download",
        },
      ],
    }),
  ];

  const items = collectSessionImageGalleryItems(messages);
  const serialized = JSON.stringify(items);

  assert.deepEqual(
    items.map((item) => item.src),
    [
      "/api/ai/artifacts/safe-attachment/download",
      "/api/ai/artifacts/safe-image/download",
    ],
  );
  assert.doesNotMatch(serialized, /http:\/\/cdn\.example\.com/);
  assert.doesNotMatch(serialized, /\.claude|%252Eclaude/i);
});

test("ChatView provides a session image gallery around chat messages", () => {
  const source = readFileSync(
    new URL("../../../layout/AppContent/ChatView.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /SessionImageGalleryProvider/);
  assert.match(source, /messages=\{messages\}/);
});

test("conversation image entry points use the session gallery when available", () => {
  const markdownSource = readFileSync(
    new URL("../MarkdownContent.tsx", import.meta.url),
    "utf8",
  );
  const userBubbleSource = readFileSync(
    new URL("../UserMessageBubble.tsx", import.meta.url),
    "utf8",
  );

  assert.match(markdownSource, /useSessionImageGallery/);
  assert.match(markdownSource, /sessionImageGallery\?\.openImage/);
  assert.match(markdownSource, /useSafeAttachmentImageSrc/);
  assert.match(markdownSource, /SafeMarkdownImage/);
  assert.match(markdownSource, /from "\.\/sessionImageSafety"/);
  assert.doesNotMatch(markdownSource, /<img[\s\S]*src=\{resolvedSrc\}/);
  assert.match(userBubbleSource, /useSessionImageGallery/);
  assert.match(userBubbleSource, /sessionImageGallery\?\.openImage/);
  assert.match(markdownSource, /setImageViewerSrc\(imageSrc\)/);
  assert.match(
    readFileSync(new URL("../sessionImageGallery.tsx", import.meta.url), "utf8"),
    /useSafeAttachmentImageSrc/,
  );
});

test("session image gallery ignores legacy reveal payloads", () => {
  const sessionGallerySource = readFileSync(
    new URL("../sessionImageGallery.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(sessionGallerySource, /reveal_file/);
  assert.doesNotMatch(sessionGallerySource, /parseFileRevealPreviewData/);
  assert.doesNotMatch(sessionGallerySource, /RevealArtifactsSummary/);
  assert.doesNotMatch(sessionGallerySource, /collectRevealArtifacts/);
  assert.doesNotMatch(sessionGallerySource, /from "\.\/revealArtifacts"/);
});

test("markdown protected platform links are not rendered as raw anchors", () => {
  const markup = renderToStaticMarkup(
    React.createElement(MarkdownContent, {
      content: "[download](/api/ai/artifacts/artifact-1/download)",
    }),
  );

  assert.doesNotMatch(
    markup,
    /href="\/api\/ai\/artifacts\/artifact-1\/download"/,
  );
});

test("markdown unsafe api and internal artifact links are not rendered as raw anchors", () => {
  const markup = renderToStaticMarkup(
    React.createElement(MarkdownContent, {
      content:
        "[users](/api/users) [secret](/api/ai/artifacts/%252Eclaude%252Fruns%252Fsecret/download?filename=secret.pdf)",
    }),
  );

  assert.doesNotMatch(markup, /href="\/api\/users"/);
  assert.doesNotMatch(markup, /href="\/api\/ai\/artifacts\//);
  assert.doesNotMatch(markup, /%252Eclaude/i);
});

test("markdown ordinary non-api file links keep native anchor behavior", () => {
  const markup = renderToStaticMarkup(
    React.createElement(MarkdownContent, {
      content: "[report](/files/report.pdf)",
    }),
  );

  assert.match(markup, /href="\/files\/report\.pdf"/);
  assert.match(markup, /target="_blank"/);
});
