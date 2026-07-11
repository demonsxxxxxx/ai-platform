import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));

test("sendMessage returns a narrow outcome and forwards one selected Skill", () => {
  const source = readFileSync(resolve(__dirname, "../../useAgent.ts"), "utf8");

  assert.match(source, /selectedSkill\?:\s*SelectedSkillRequest/);
  assert.match(source, /sessionApi\.submitChat\([\s\S]*selectedSkill/);
  assert.match(source, /Promise<SubmissionOutcome>/);
  assert.match(source, /status:\s*"accepted"/);
  assert.match(source, /status:\s*"recoverable_error"/);
});

test("recoverable admission errors remove optimistic messages and remain explicit", () => {
  const source = readFileSync(resolve(__dirname, "../../useAgent.ts"), "utf8");
  const selectionSource = readFileSync(
    resolve(__dirname, "../../useSelectedSkillTask.ts"),
    "utf8",
  );

  for (const code of [
    "skill_selection_stale",
    "capability_not_authorized",
    "file_required_for_skill",
  ]) {
    assert.match(selectionSource, new RegExp(code));
  }
  assert.match(source, /SELECTED_SKILL_RECOVERABLE_CODES/);
  assert.match(source, /setMessages\(previousMessages\)/);
  assert.equal(source.match(/sessionApi\.submitChat/g)?.length, 1);
});

test("accepted fire-and-forget stream handles setup rejection before cleanup", () => {
  const source = readFileSync(resolve(__dirname, "../../useAgent.ts"), "utf8");

  assert.match(
    source,
    /void connectToSSE\([\s\S]*?\)\s*\.catch\([\s\S]*?\)\s*\.finally\(/,
  );
  assert.match(source, /setConnectionStatus\("disconnected"\)/);
});
