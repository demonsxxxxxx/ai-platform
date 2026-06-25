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

const { resolveExposedSkillPermissions } = await import(
  "../../../../hooks/useSkills.ts"
);

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
      catalogPermissionsKnown: false,
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
      catalogPermissionsKnown: false,
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
      catalogPermissionsKnown: false,
      enableSkillsSettingKnown: projection.known,
      enableSkillsSetting: projection.value ?? false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("keeps composer Skills available while probing public catalog after login", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: false,
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("enables composer Skills from public catalog effective permissions", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: false,
      catalogEffectivePermissions: ["skill:read"],
      catalogPermissionsKnown: true,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("disables composer Skills when catalog permissions deny stale auth projection", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: true,
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: true,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: false,
    },
  );
});

test("keeps composer Skills enabled from auth projection before catalog resolves", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: true,
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("recognizes catalog skill admin as composer Skills read permission", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: false,
      catalogEffectivePermissions: ["skill:admin"],
      catalogPermissionsKnown: true,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: false,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: true,
    },
  );
});

test("keeps composer Skills fail-closed after catalog resolves without read permission", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      canReadSkills: false,
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: true,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: true,
      enableComposerSkills: false,
    },
  );
});

test("keeps composer Skills fail-closed while logged out after catalog resolves", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: false,
      canReadSkills: false,
      catalogEffectivePermissions: ["skill:read"],
      catalogPermissionsKnown: true,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: false,
      enableComposerSkills: false,
    },
  );
});

test("keeps composer Skills fail-closed while logged out before catalog resolves", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: false,
      canReadSkills: true,
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: false,
      enableComposerSkills: false,
    },
  );
});

test("keeps composer Skills fail-closed while logged out before catalog permissions resolve", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: false,
      canReadSkills: false,
      catalogEffectivePermissions: ["skill:read"],
      catalogPermissionsKnown: false,
      enableSkillsSettingKnown: false,
      enableSkillsSetting: true,
    }),
    {
      shouldFetchSkills: false,
      enableComposerSkills: false,
    },
  );
});

test("does not expose stale skill permissions when catalog fetch is inactive", () => {
  assert.deepEqual(
    resolveExposedSkillPermissions({
      enabled: false,
      permissionsValid: true,
      effectivePermissions: ["skill:read"],
      effectivePermissionsKnown: true,
    }),
    {
      effectivePermissions: [],
      effectivePermissionsKnown: false,
    },
  );
});

test("does not expose stale skill permissions before the current catalog fetch resolves", () => {
  assert.deepEqual(
    resolveExposedSkillPermissions({
      enabled: true,
      permissionsValid: false,
      effectivePermissions: ["skill:read"],
      effectivePermissionsKnown: true,
    }),
    {
      effectivePermissions: [],
      effectivePermissionsKnown: false,
    },
  );
});

test("exposes skill permissions only after the current catalog fetch resolves", () => {
  assert.deepEqual(
    resolveExposedSkillPermissions({
      enabled: true,
      permissionsValid: true,
      effectivePermissions: ["skill:read"],
      effectivePermissionsKnown: true,
    }),
    {
      effectivePermissions: ["skill:read"],
      effectivePermissionsKnown: true,
    },
  );
});
