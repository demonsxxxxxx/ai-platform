import assert from "node:assert/strict";
import test from "node:test";

import {
  buildEffectiveSkills,
  countEnabledSkills,
  resolveSettingsBooleanProjection,
  resolveComposerSkillsAvailability,
} from "../skillAvailability.ts";
import type {
  SettingItem,
  SettingsResponse,
  SkillResponse,
} from "../../../../types";

function skill(name: string, enabled = true): SkillResponse {
  return {
    name,
    description: "",
    tags: [],
    enabled,
    source: "manual",
    files: {},
    file_count: 1,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: true,
  };
}

function booleanSetting(key: string, value: boolean): SettingItem {
  return {
    key,
    value,
    type: "boolean",
    category: "skills",
    subcategory: "general",
    description: key,
    default_value: true,
    requires_restart: false,
    is_sensitive: false,
    frontend_visible: true,
  };
}

test("limits persona skills by whitelist and then applies disabled skills", () => {
  const result = buildEffectiveSkills({
    skills: [skill("planner"), skill("writer"), skill("other")],
    skillsLoading: false,
    personaSkillNames: ["planner", "writer"],
    disabledSkillNames: ["writer"],
  });

  assert.deepEqual(
    result.map((item) => [item.name, item.enabled]),
    [
      ["planner", true],
      ["writer", false],
    ],
  );
  assert.equal(countEnabledSkills(result), 1);
});

test("falls back to disabled-skills mode without a persona whitelist", () => {
  const result = buildEffectiveSkills({
    skills: [skill("planner"), skill("writer"), skill("globally-off", false)],
    skillsLoading: false,
    disabledSkillNames: ["writer"],
  });

  assert.deepEqual(
    result.map((item) => [item.name, item.enabled]),
    [
      ["planner", true],
      ["writer", false],
    ],
  );
  assert.equal(countEnabledSkills(result), 1);
});

test("keeps composer Skills usable when settings projection is degraded but skills are readable", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      canReadSkills: true,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("keeps composer Skills usable when settings response omits ENABLE_SKILLS", () => {
  const projection = resolveSettingsBooleanProjection(
    { settings: {} as SettingsResponse["settings"] },
    "ENABLE_SKILLS",
  );

  assert.deepEqual(projection, { known: false, value: undefined });
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      canReadSkills: true,
      enableSkillsSettingKnown: projection.known,
      enableSkillsSetting: projection.value ?? false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("disables composer Skills only when settings explicitly disable Skills", () => {
  const projection = resolveSettingsBooleanProjection(
    {
      settings: {
        skills: [booleanSetting("ENABLE_SKILLS", false)],
      } as SettingsResponse["settings"],
    },
    "ENABLE_SKILLS",
  );

  assert.deepEqual(projection, { known: true, value: false });
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      canReadSkills: true,
      enableSkillsSettingKnown: projection.known,
      enableSkillsSetting: projection.value ?? true,
    }),
    {
      shouldFetchSkills: false,
      enableComposerSkills: false,
    },
  );
});

test("keeps composer Skills fail-closed without skill read permission", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      canReadSkills: false,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: false,
      enableComposerSkills: false,
    },
  );
});
