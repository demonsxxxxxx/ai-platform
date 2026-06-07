import assert from "node:assert/strict";
import test from "node:test";

import {
  createSafeAttachmentImageObjectUrl,
  resolveSafeAttachmentImageSrc,
} from "../attachmentImageSafety.ts";

test("attachment image resolver accepts artifact preview/download and upload file routes", () => {
  assert.equal(
    resolveSafeAttachmentImageSrc("/api/ai/artifacts/image-1/preview"),
    "/api/ai/artifacts/image-1/preview",
  );
  assert.equal(
    resolveSafeAttachmentImageSrc("/api/ai/artifacts/image-1/download"),
    "/api/ai/artifacts/image-1/download",
  );
  assert.equal(
    resolveSafeAttachmentImageSrc("/api/upload/file/image-1.png"),
    "/api/upload/file/image-1.png",
  );
});

test("attachment image object url fetches platform images through authenticated request", async () => {
  const requested: string[] = [];
  const objectUrl = await createSafeAttachmentImageObjectUrl(
    "/api/ai/artifacts/image-1/preview",
    "image/png",
    {
      fetchOptions: {
        authenticatedRequest: async (url) => {
          requested.push(String(url));
          return new Response(new Uint8Array([1, 2, 3]), {
            status: 200,
            headers: { "Content-Type": "image/png" },
          });
        },
      },
      createObjectURL: (blob) => {
        assert.equal(blob.type, "image/png");
        return "blob:authenticated-image";
      },
    },
  );

  assert.equal(objectUrl, "blob:authenticated-image");
  assert.deepEqual(requested, ["/api/ai/artifacts/image-1/preview"]);
});

test("attachment image resolver rejects external, arbitrary api, internal, and raw storage values", () => {
  assert.equal(
    resolveSafeAttachmentImageSrc("https://cdn.example.com/image.png"),
    null,
  );
  assert.equal(
    resolveSafeAttachmentImageSrc(
      "http://127.0.0.1:8020/api/upload/file/image.png",
    ),
    null,
  );
  assert.equal(resolveSafeAttachmentImageSrc("/api/users/avatar.png"), null);
  assert.equal(resolveSafeAttachmentImageSrc("/api/chat/stream"), null);
  assert.equal(resolveSafeAttachmentImageSrc("storage.key"), null);
  assert.equal(resolveSafeAttachmentImageSrc("runtime.path"), null);
});
