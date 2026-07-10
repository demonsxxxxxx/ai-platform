import assert from "node:assert/strict";
import test from "node:test";

import {
  inferUploadCategory,
  validateUploadBatch,
} from "../FileUploadButton.tsx";
import { Permission } from "../../../types";

function createFileLike(
  overrides: Partial<{ name: string; type: string }> = {},
): { name: string; type: string } {
  return {
    name: overrides.name ?? "report.pdf",
    type: overrides.type ?? "application/pdf",
  };
}

test("inferUploadCategory honors the explicitly selected category", () => {
  assert.equal(
    inferUploadCategory(
      createFileLike({ name: "clip.mp4", type: "video/mp4" }),
      "video",
    ),
    "video",
  );
});

test("validateUploadBatch blocks the whole batch when any file lacks permission", () => {
  const result = validateUploadBatch(
    [
      createFileLike({ name: "safe.png", type: "image/png" }),
      createFileLike({ name: "notes.pdf", type: "application/pdf" }),
    ],
    {
      hasPermission(permission) {
        return permission !== Permission.FILE_UPLOAD_DOCUMENT;
      },
    },
  );

  assert.deepEqual(result, {
    ok: false,
    reason: "permission_denied",
    blockedCategory: "document",
    blockedFileName: "notes.pdf",
  });
});

test("validateUploadBatch blocks active html uploads even when the mime metadata looks safe", () => {
  const result = validateUploadBatch(
    [
      createFileLike({
        name: "payload.html",
        type: "text/plain",
      }),
    ],
    {
      requestedCategory: "document",
      hasPermission() {
        return true;
      },
    },
  );

  assert.deepEqual(result, {
    ok: false,
    reason: "active_content_blocked",
    blockedCategory: "document",
    blockedFileName: "payload.html",
  });
});

test("validateUploadBatch blocks active svg uploads for image selections", () => {
  const result = validateUploadBatch(
    [
      createFileLike({
        name: "payload.svg",
        type: "image/svg+xml",
      }),
    ],
    {
      requestedCategory: "image",
      hasPermission() {
        return true;
      },
    },
  );

  assert.deepEqual(result, {
    ok: false,
    reason: "active_content_blocked",
    blockedCategory: "image",
    blockedFileName: "payload.svg",
  });
});

test("validateUploadBatch fails closed when permission probing throws", () => {
  const result = validateUploadBatch([createFileLike()], {
    hasPermission() {
      throw new Error("permission backend unavailable");
    },
  });

  assert.deepEqual(result, {
    ok: false,
    reason: "permission_probe_failed",
    blockedCategory: "document",
    blockedFileName: "report.pdf",
  });
});
