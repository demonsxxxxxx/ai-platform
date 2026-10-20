import assert from "node:assert/strict";
import test from "node:test";
import { submitComposerMessage } from "../composerSubmission";
import type { ChatInputDraftSnapshot, ChatInputProps } from "../../chatInputTypes";
import type { SubmissionOutcome } from "../../../../hooks/useAgent/types";

test("accepted submit keeps the full payload and clears only its captured draft and attachments", async () => {
  let resolve!: (value: SubmissionOutcome) => void;
  const receipt = new Promise<SubmissionOutcome>((done) => { resolve = done; });
  const snapshot: ChatInputDraftSnapshot = { value: "task", appliedInitialDraftKey: null, scopeKey: "session-a", scopeRevision: 1,
    revision: 1, selectedSkillState: undefined, selectedSkillRevision: 0, pendingScopeHandoff: false, apply: () => {} };
  const attachment = { id: "file-a", key: "authorized-ref", name: "a.txt", type: "document" as const, mimeType: "text/plain", size: 1 };
  let attachments: NonNullable<ChatInputProps["attachments"]> = [attachment];
  let payload: Parameters<ChatInputProps["onSend"]> | undefined;
  let restored = "";
  const transaction = submitComposerMessage({ content: "task", snapshot, attachments, allowed: true, lock: { current: null },
    agentOptionValues: { model: "chosen-model", enable_thinking: true }, onSend: (...args) => { payload = args; return receipt; },
    setInput: (value) => { snapshot.value = value; }, setAttachments: (update) => { attachments = update(attachments); },
    pushHistory: () => {}, restoreText: (value) => { restored = value; } });
  snapshot.value = "next task";
  snapshot.revision += 1;
  attachments = [...attachments, { ...attachment, id: "file-b", name: "b.txt" }];
  resolve({ status: "accepted" });
  await transaction;
  assert.deepEqual(payload, ["task", { model: "chosen-model", enable_thinking: true }, [attachment], null]);
  assert.equal(snapshot.value, "next task");
  assert.equal(restored, "next task");
  assert.deepEqual(attachments.map((file) => file.id), ["file-b"]);
});

for (const outcome of ["accepted", "recoverable_error"] as const) {
  test(`late ${outcome} never mutates another account/session owner`, async () => {
    let resolve!: (value: SubmissionOutcome) => void;
    const receipt = new Promise<SubmissionOutcome>((done) => { resolve = done; });
    const snapshot: ChatInputDraftSnapshot = { value: "old", appliedInitialDraftKey: null, scopeKey: "old-session", scopeRevision: 1,
      revision: 1, selectedSkillState: undefined, selectedSkillRevision: 0, pendingScopeHandoff: false, apply: () => {} };
    const writes: string[] = [];
    const transaction = submitComposerMessage({ content: "old", snapshot, attachments: [], allowed: true, lock: { current: null }, onSend: () => receipt,
      setInput: () => writes.push("draft"), setAttachments: () => writes.push("attachments"), pushHistory: () => writes.push("history"),
      restoreText: () => writes.push("native"), onClearSelectedSkill: () => writes.push("skill"), onSelectedSkillRecoverable: async () => writes.push("recovery") });
    snapshot.scopeKey = "new-session";
    snapshot.scopeRevision = 2;
    snapshot.value = "new account draft";
    resolve(outcome === "accepted" ? { status: "accepted" } : { status: "recoverable_error", code: "file_required_for_skill" });
    await transaction;
    assert.deepEqual(writes, []);
    assert.equal(snapshot.value, "new account draft");
  });
}
