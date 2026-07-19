import assert from "node:assert/strict";
import test from "node:test";
import { downloadAuthenticatedFile } from "../download.ts";

test("downloads protected relative artifact URLs through an authenticated request", async () => {
  const appended: unknown[] = [];
  const removed: unknown[] = [];
  const clicks: string[] = [];
  const revoked: string[] = [];
  const scheduledRevocations: Array<() => void> = [];

  const anchor = {
    href: "",
    download: "",
    click() {
      clicks.push(this.href);
    },
  };

  const fakeDocument = {
    createElement(tagName: string) {
      assert.equal(tagName, "a");
      return anchor;
    },
    body: {
      appendChild(element: unknown) {
        appended.push(element);
      },
      removeChild(element: unknown) {
        removed.push(element);
      },
    },
  } as unknown as Document;

  const result = await downloadAuthenticatedFile(
    "/api/ai/artifacts/art-reviewed/download",
    "fallback.docx",
    {
      documentRef: fakeDocument,
      createObjectURL(blob) {
        assert.equal(blob.size, 13);
        return "blob:artifact-download";
      },
      revokeObjectURL(url) {
        revoked.push(url);
      },
      scheduleRevoke(callback) {
        scheduledRevocations.push(callback);
      },
      request: async (input, init) => {
        assert.equal(input, "/api/ai/artifacts/art-reviewed/download");
        assert.equal(init?.method, "GET");
        return new Response("reviewed-docx", {
          headers: {
            "Content-Disposition": 'attachment; filename="reviewed.docx"',
            "Content-Type":
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
          },
        });
      },
    },
  );

  assert.equal(result.filename, "reviewed.docx");
  assert.equal(anchor.href, "blob:artifact-download");
  assert.equal(anchor.download, "reviewed.docx");
  assert.deepEqual(clicks, ["blob:artifact-download"]);
  assert.deepEqual(appended, [anchor]);
  assert.deepEqual(removed, [anchor]);
  assert.deepEqual(revoked, []);
  assert.equal(scheduledRevocations.length, 1);

  scheduledRevocations[0]();

  assert.deepEqual(revoked, ["blob:artifact-download"]);
});
