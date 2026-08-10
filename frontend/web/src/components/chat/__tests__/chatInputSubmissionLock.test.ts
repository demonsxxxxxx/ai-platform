import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import {
  reconcileChatInputSubmissionLock,
  releaseChatInputSubmissionLock,
  tryAcquireChatInputSubmissionLock,
} from "../chatInputSubmissionLock";

test("terminal idle state releases a stale composer submission lock", () => {
  const lock: { current: symbol | null } = { current: null };

  const firstToken = tryAcquireChatInputSubmissionLock(lock);
  assert.equal(typeof firstToken, "symbol");
  assert.equal(tryAcquireChatInputSubmissionLock(lock), null);

  reconcileChatInputSubmissionLock(lock, true);
  assert.equal(lock.current, firstToken);

  reconcileChatInputSubmissionLock(lock, false);
  assert.equal(lock.current, null);
  const secondToken = tryAcquireChatInputSubmissionLock(lock);
  assert.equal(typeof secondToken, "symbol");

  releaseChatInputSubmissionLock(lock, firstToken!);
  assert.equal(lock.current, secondToken);
  releaseChatInputSubmissionLock(lock, secondToken!);
  assert.equal(lock.current, null);
});

test("ChatInput reconciles and releases the same guarded lock", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/chat/ChatInput.tsx"),
    "utf8",
  );

  assert.match(
    source,
    /reconcileChatInputSubmissionLock\(isSubmittingRef, isLoading\)/,
  );
  assert.match(source, /tryAcquireChatInputSubmissionLock\(isSubmittingRef\)/);
  assert.match(
    source,
    /releaseChatInputSubmissionLock\(isSubmittingRef, submissionToken\)/,
  );
});
