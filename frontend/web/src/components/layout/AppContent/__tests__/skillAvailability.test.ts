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
      isAuthenticated: true,
      canReadSkills: true,
      catalogEffectivePermissions: [],
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
      isAuthenticated: true,
      canReadSkills: true,
      catalogEffectivePermissions: [],
      enableSkillsSettingKnown: projection.known,
      enableSkillsSetting: projection.value ?? false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("keeps composer Skills reachable when legacy ENABLE_SKILLS is explicitly disabled", () => {
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
      isAuthenticated: true,
      canReadSkills: true,
      catalogEffectivePermissions: [],
      enableSkillsSettingKnown: projection.known,
      enableSkillsSetting: projection.value ?? false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("probes public Skills after login when auth projection is stale", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: false,
      catalogEffectivePermissions: [],
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: false,
    },
  );
});

test("enables composer Skills from public catalog effective permissions", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: false,
      catalogEffectivePermissions: ["skill:read"],
      enableSkillsSettingKnown: false,
      enableSkillsSetting: false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("keeps composer Skills fail-closed while logged out", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: false,
      canReadSkills: false,
      catalogEffectivePermissions: ["skill:read"],
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: false,
      enableComposerSkills: false,
    },
  );
});
