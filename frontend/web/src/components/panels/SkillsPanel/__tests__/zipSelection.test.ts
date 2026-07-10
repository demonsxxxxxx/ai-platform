import test from "node:test";
import assert from "node:assert/strict";

import {
  canSelectZipSkill,
  initialZipSkillSelection,
  toggleZipSkillSelection,
  type ZipSkillPreview,
} from "../zipSelection.ts";

const existingSkill: ZipSkillPreview = {
  name: "existing-skill",
  description: "Existing catalog skill",
  file_count: 1,
  files: ["SKILL.md"],
  already_exists: true,
};

const newSkill: ZipSkillPreview = {
  name: "new-skill",
  description: "New uploaded skill",
  file_count: 1,
  files: ["SKILL.md"],
  already_exists: false,
};

test("admin ZIP selection only auto-selects new skills", () => {
  assert.deepEqual(
    initialZipSkillSelection([existingSkill, newSkill], true),
    ["new-skill"],
  );
  assert.equal(canSelectZipSkill(existingSkill, true), false);
  assert.equal(canSelectZipSkill(newSkill, true), true);
});

test("admin ZIP selection ignores existing skills during toggle", () => {
  assert.deepEqual(
    toggleZipSkillSelection([], "existing-skill", [existingSkill, newSkill], true),
    [],
  );
  assert.deepEqual(
    toggleZipSkillSelection([], "new-skill", [existingSkill, newSkill], true),
    ["new-skill"],
  );
});

test("normal ZIP import still selects existing catalog skills only", () => {
  assert.deepEqual(
    initialZipSkillSelection([existingSkill, newSkill], false),
    ["existing-skill"],
  );
  assert.equal(canSelectZipSkill(existingSkill, false), true);
  assert.equal(canSelectZipSkill(newSkill, false), false);
});
