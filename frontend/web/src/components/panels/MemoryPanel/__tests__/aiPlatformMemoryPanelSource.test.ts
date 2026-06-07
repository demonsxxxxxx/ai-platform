import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(
  new URL("../index.tsx", import.meta.url),
  "utf8",
);

test("MemoryPanel is wired to ai-platform memory projections instead of legacy memory CRUD", () => {
  for (const requiredSymbol of [
    "fetchMemoryPolicy",
    "setMemoryPolicy",
    "fetchMemoryRecords",
    "fetchAdminMemoryPolicies",
    "cleanupExpiredMemoryRecords",
  ]) {
    assert.match(source, new RegExp(`\\b${requiredSymbol}\\b`));
  }

  assert.match(source, /\bsessionId\b/);
  assert.doesNotMatch(source, /"developer"/);
  assert.doesNotMatch(source, /\bmemoryApi\./);
  assert.doesNotMatch(source, /\bMemoryEditor\b/);
  assert.doesNotMatch(source, /\bimportInputRef\b/);
  assert.doesNotMatch(source, /lambchat-memory-/);
});
