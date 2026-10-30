import assert from "node:assert/strict";
import test from "node:test";
import { buildProfileDriveTree } from "../profileDriveTree";
import { buildSubmitChatBody } from "../../../services/api/session";
import { createOptimisticMessagesForSend } from "../../../hooks/useAgent/optimisticMessages";
import type { ProfileDriveFileEntry } from "../../../services/api/profileDrive";

const folder = (path: string): ProfileDriveFileEntry => ({ path, name: path.split("/").at(-1)!, type: "directory", size: null, lastModifiedUtc: "2026-10-29T00:00:00Z" });
const a = folder("a"), b = folder("a/b"), c = folder("a/b/c");
const file: ProfileDriveFileEntry = { ...folder("a/b/c/long-name.pdf"), type: "file", size: 42 };
const initial = {
  entries: [a], directories: new Map([[a.path, { entries: [b], truncated: false }], [b.path, { entries: [c], truncated: false }], [c.path, { entries: [file], truncated: false }]]),
  expanded: new Set([a.path, b.path, c.path]), loading: new Set<string>(), errors: new Map<string, string>(), focusTrail: [], filter: "",
};
test("drive origin remains Composer-local, not a submission or message field", () => {
  const attachment = { id: "a", key: "file-a", name: "report.pdf", type: "document" as const, mimeType: "application/pdf", size: 42, profileDriveReference: { source_id: "public" as const, path: "Projects/report.pdf" } };
  const body = buildSubmitChatBody({ message: "Review", attachments: [attachment] });
  const projected = (body.attachments as object[])[0];
  assert.equal("profileDriveReference" in projected, false);
  const optimistic = createOptimisticMessagesForSend({ previousMessages: [], content: "Review", attachments: [attachment] });
  assert.equal("profileDriveReference" in optimistic.messages[0].attachments![0], false);
  assert.ok(attachment.profileDriveReference, "Composer original stays intact");
});
test("compacts only expanded complete known chains and keeps their real paths", () => {
  const rows = buildProfileDriveTree(initial);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].kind === "entry" && rows[0].chain.map((entry) => entry.path).join(), "a,a/b,a/b/c");
  assert.equal(rows[1].kind === "entry" && rows[1].entry.path, file.path);
  assert.equal(rows[1].depth, 1);
  const truncated = buildProfileDriveTree({ ...initial, directories: new Map(initial.directories).set("a", { entries: [b], truncated: true }) });
  assert.equal(truncated[0].kind === "entry" && truncated[0].entry.path, "a");
  assert.ok(truncated.some((row) => row.kind === "status" && row.text === "目录内容已截断"));
  const collapsed = buildProfileDriveTree({ ...initial, expanded: new Set<string>() });
  assert.equal(collapsed.length, 1);
  assert.equal(collapsed[0].kind === "entry" && collapsed[0].entry.path, "a");
});
test("focus resets visual depth without dropping loading, error, empty or truncation state", () => {
  const focusTrail = [a, b, c];
  const rows = buildProfileDriveTree({ ...initial, focusTrail });
  assert.equal(rows[0].depth, 0);
  assert.deepEqual(rows[0].kind === "entry" && rows[0].ancestors, focusTrail);
  assert.equal(buildProfileDriveTree({ ...initial, focusTrail, loading: new Set([c.path]) })[0].kind, "status");
  const failure = buildProfileDriveTree({ ...initial, focusTrail, errors: new Map([[c.path, "没有权限"]]) })[0];
  assert.equal(failure.kind, "error");
  assert.equal(failure.kind === "error" && failure.entry.path, c.path);
  const empty = buildProfileDriveTree({ ...initial, focusTrail, directories: new Map(initial.directories).set(c.path, { entries: [], truncated: false }) });
  assert.equal(empty[0].kind === "status" && empty[0].text, "目录为空");
});
test("filter retains expanded original directories without scanning unknown children", () => {
  assert.equal(buildProfileDriveTree({ ...initial, filter: "long" }).length, 4);
  const unloaded = buildProfileDriveTree({ ...initial, directories: new Map() });
  assert.equal(unloaded.length, 1);
});
