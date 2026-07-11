import assert from "node:assert/strict";
import test from "node:test";

import * as selectedSkillTask from "../useSelectedSkillTask.ts";
import type { PublicSkillResponse } from "../../types/skill.ts";

function skill(
  name: string,
  expectedVersion: string,
  requiresFile = false,
): PublicSkillResponse {
  return {
    name,
    expected_version: expectedVersion,
    input_modes: requiresFile ? ["docx"] : ["chat"],
    requires_file: requiresFile,
    description: `${name} description`,
    tags: [],
    enabled: true,
    source: "marketplace",
    files: {},
    file_count: 1,
    installed_from: "marketplace",
    is_published: true,
    marketplace_is_active: true,
  };
}

test("selecting a Skill replaces the prior task selection", () => {
  assert.equal(typeof selectedSkillTask.createSelectedSkillTaskState, "function");
  assert.equal(typeof selectedSkillTask.selectedSkillTaskReducer, "function");

  const first = selectedSkillTask.selectedSkillTaskReducer(
    selectedSkillTask.createSelectedSkillTaskState(),
    { type: "select", skill: skill("review", "hash-review") },
  );
  const second = selectedSkillTask.selectedSkillTaskReducer(first, {
    type: "select",
    skill: skill("research", "hash-research"),
  });

  assert.equal(second.selectedSkill?.name, "research");
  assert.equal(second.status, "confirmed");
  assert.equal(second.requiresReconfirmation, false);
});

test("stale selection survives refresh without silently upgrading", () => {
  const selected = selectedSkillTask.selectedSkillTaskReducer(
    selectedSkillTask.createSelectedSkillTaskState(),
    { type: "select", skill: skill("review", "hash-old") },
  );
  const stale = selectedSkillTask.selectedSkillTaskReducer(selected, {
    type: "recoverable_error",
    code: "skill_selection_stale",
  });
  const refreshed = selectedSkillTask.selectedSkillTaskReducer(stale, {
    type: "refresh_complete",
    skills: [skill("review", "hash-current")],
  });

  assert.equal(refreshed.selectedSkill?.expected_version, "hash-old");
  assert.equal(refreshed.status, "stale");
  assert.equal(refreshed.requiresReconfirmation, true);

  const reconfirmed = selectedSkillTask.selectedSkillTaskReducer(refreshed, {
    type: "select",
    skill: skill("review", "hash-current"),
  });
  assert.equal(reconfirmed.selectedSkill?.expected_version, "hash-current");
  assert.equal(reconfirmed.status, "confirmed");
});

test("unauthorized or hidden cached selection clears identity and blocks fallback", () => {
  const selected = selectedSkillTask.selectedSkillTaskReducer(
    selectedSkillTask.createSelectedSkillTaskState(),
    { type: "select", skill: skill("private-review", "hash-private") },
  );
  const denied = selectedSkillTask.selectedSkillTaskReducer(selected, {
    type: "recoverable_error",
    code: "capability_not_authorized",
  });

  assert.equal(denied.selectedSkill, null);
  assert.equal(denied.status, "denied");
  assert.equal(denied.requiresReconfirmation, true);

  const hiddenAfterRefresh = selectedSkillTask.selectedSkillTaskReducer(
    selected,
    { type: "refresh_complete", skills: [] },
  );
  assert.equal(hiddenAfterRefresh.selectedSkill, null);
  assert.equal(hiddenAfterRefresh.status, "denied");
  assert.equal(hiddenAfterRefresh.requiresReconfirmation, true);
});

test("requires-file preflight accepts only a completed existing upload", () => {
  assert.equal(typeof selectedSkillTask.getSelectedSkillPreflightError, "function");
  const state = selectedSkillTask.selectedSkillTaskReducer(
    selectedSkillTask.createSelectedSkillTaskState(),
    { type: "select", skill: skill("document-review", "hash-docx", true) },
  );

  assert.equal(
    selectedSkillTask.getSelectedSkillPreflightError(state, []),
    "file_required_for_skill",
  );
  assert.equal(
    selectedSkillTask.getSelectedSkillPreflightError(state, [
      { id: "uploading", isUploading: true },
    ]),
    "file_required_for_skill",
  );
  assert.equal(
    selectedSkillTask.getSelectedSkillPreflightError(state, [
      { id: "file-1", isUploading: false },
    ]),
    null,
  );
});

test("file-required recovery materializes the selected request once a file is ready", () => {
  const selected = selectedSkillTask.selectedSkillTaskReducer(
    selectedSkillTask.createSelectedSkillTaskState(),
    { type: "select", skill: skill("document-review", "hash-docx", true) },
  );
  const fileRequired = selectedSkillTask.selectedSkillTaskReducer(selected, {
    type: "recoverable_error",
    code: "file_required_for_skill",
  });

  assert.deepEqual(
    selectedSkillTask.prepareSelectedSkillSubmission(fileRequired, [
      { id: "file-1", isUploading: false },
    ]),
    {
      error: null,
      request: {
        skill_id: "document-review",
        expected_version: "hash-docx",
      },
    },
  );
  assert.deepEqual(
    selectedSkillTask.prepareSelectedSkillSubmission(fileRequired, []),
    { error: "file_required_for_skill", request: null },
  );
});

test("only an eligible selection materializes through the atomic submission helper", () => {
  const confirmed = selectedSkillTask.selectedSkillTaskReducer(
    selectedSkillTask.createSelectedSkillTaskState(),
    { type: "select", skill: skill("review", "hash-review") },
  );
  assert.deepEqual(
    selectedSkillTask.prepareSelectedSkillSubmission(confirmed, []),
    {
      error: null,
      request: {
        skill_id: "review",
        expected_version: "hash-review",
      },
    },
  );

  const stale = selectedSkillTask.selectedSkillTaskReducer(confirmed, {
    type: "recoverable_error",
    code: "skill_selection_stale",
  });
  assert.deepEqual(
    selectedSkillTask.prepareSelectedSkillSubmission(stale, []),
    {
      error: "skill_selection_stale",
      request: null,
    },
  );
});
