import assert from "node:assert/strict";
import test from "node:test";

import { UploadRequestError } from "../../services/api/upload.ts";
import { settleUploadFailure } from "../useFileUpload.ts";

const translate = (key: string) => `translated:${key}`;

test("upload failures use bounded copy and remove only the matching temporary attachment", () => {
  const cases = [
    {
      error: new UploadRequestError("file_too_large", 413, "file_too_large"),
      expected: "translated:fileUpload.serverFileTooLarge",
    },
    {
      error: new UploadRequestError(
        "unsupported_file_type",
        415,
        "unsupported_file_type",
      ),
      expected: "translated:fileUpload.serverUnsupportedFileType",
    },
    {
      error: new UploadRequestError("recoverable", 500),
      expected: "translated:fileUpload.uploadFailedRecoverable",
    },
    {
      error: new Error("backend detail must not reach a toast"),
      expected: "translated:fileUpload.uploadFailedRecoverable",
    },
  ];

  for (const { error, expected } of cases) {
    let attachmentIds = ["temp-target", "existing-attachment"];
    let cleanupCalls = 0;
    const message = settleUploadFailure(error, translate, () => {
      cleanupCalls += 1;
      attachmentIds = attachmentIds.filter((id) => id !== "temp-target");
    });

    assert.equal(message, expected);
    assert.equal(cleanupCalls, 1);
    assert.deepEqual(attachmentIds, ["existing-attachment"]);
  }
});

test("cancelled upload failures are silent and do not mutate removed attachments", () => {
  let attachmentIds = ["existing-attachment"];
  let cleanupCalls = 0;
  const message = settleUploadFailure(
    new UploadRequestError("cancelled"),
    translate,
    () => {
      cleanupCalls += 1;
      attachmentIds = attachmentIds.filter((id) => id !== "temp-target");
    },
  );

  assert.equal(message, null);
  assert.equal(cleanupCalls, 0);
  assert.deepEqual(attachmentIds, ["existing-attachment"]);
});
