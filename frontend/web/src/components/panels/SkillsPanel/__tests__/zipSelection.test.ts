import test from "node:test";
import assert from "node:assert/strict";

import {
  adminReleaseActionForStatus,
  canSelectZipSkill,
  initialZipSkillSelection,
  toggleZipSkillSelection,
  type ZipSkillPreview,
} from "../zipSelection.ts";

test("admin release resumes from the authoritative lifecycle state", () => {
  assert.equal(adminReleaseActionForStatus("draft"), "review");
  assert.equal(adminReleaseActionForStatus("reviewed"), "promote");
  assert.equal(adminReleaseActionForStatus("released"), "refresh");
  assert.equal(adminReleaseActionForStatus("active"), "blocked");
  assert.equal(adminReleaseActionForStatus("disabled"), "blocked");
  assert.equal(adminReleaseActionForStatus("unknown"), "blocked");
});

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

test("admin ZIP release selects exactly one new or existing Skill", () => {
  assert.deepEqual(
    initialZipSkillSelection([existingSkill, newSkill], true),
    ["existing-skill"],
  );
  assert.equal(canSelectZipSkill(existingSkill, true), true);
  assert.equal(canSelectZipSkill(newSkill, true), true);
});

test("admin ZIP release replaces the one selected Skill", () => {
  assert.deepEqual(
    toggleZipSkillSelection([], "existing-skill", [existingSkill, newSkill], true),
    ["existing-skill"],
  );
  assert.deepEqual(
    toggleZipSkillSelection(
      ["existing-skill"],
      "new-skill",
      [existingSkill, newSkill],
      true,
    ),
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
