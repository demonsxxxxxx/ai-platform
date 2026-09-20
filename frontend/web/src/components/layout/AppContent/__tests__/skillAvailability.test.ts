import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import {
  buildEffectiveSkills,
  countEnabledSkills,
  resolveComposerSkillsAvailability,
} from "../skillAvailability.ts";
import type {
  PublicSkillResponse,
  SkillResponse,
} from "../../../../types";

const { resolveExposedSkillPermissions, resolveSkillsAfterListFailure } =
  await import("../../../../hooks/useSkills.ts");

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

test("limits skills by whitelist and then applies disabled skills", () => {
  const result = buildEffectiveSkills({
    skills: [skill("planner"), skill("writer"), skill("other")],
    skillsLoading: false,
    allowedSkillNames: ["planner", "writer"],
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

test("composer skill availability never re-adds hidden skills via session toggles", () => {
  const result = buildEffectiveSkills({
    skills: [skill("planner"), skill("writer")],
    skillsLoading: false,
    allowedSkillNames: ["planner", "hidden-skill"],
    disabledSkillNames: ["hidden-skill"],
  });

  assert.deepEqual(
    result.map((item) => item.name),
    ["planner"],
  );
});

test("falls back to disabled-skills mode without a whitelist", () => {
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

test("keeps composer Skills usable while catalog permissions are unresolved", () => {
  assert.deepEqual(
    resolveComposerSkillsAvailability({
      isAuthenticated: true,
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
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
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
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
      catalogEffectivePermissions: ["skill:read"],
      catalogPermissionsKnown: true,
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
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: true,
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
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
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
      catalogEffectivePermissions: ["skill:admin"],
      catalogPermissionsKnown: true,
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
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: true,
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
      catalogEffectivePermissions: ["skill:read"],
      catalogPermissionsKnown: true,
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
      catalogEffectivePermissions: [],
      catalogPermissionsKnown: false,
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
      catalogEffectivePermissions: ["skill:read"],
      catalogPermissionsKnown: false,
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

test("authorized composer catalog failure clears cached Skill identities", () => {
  const cached = [
    {
      ...skill("cached-authorized-skill"),
      expected_version: "hash-old",
      input_modes: ["chat"],
      requires_file: false,
    } satisfies PublicSkillResponse,
  ];

  assert.deepEqual(resolveSkillsAfterListFailure(cached, true), []);
  assert.equal(resolveSkillsAfterListFailure(cached, false), cached);
});

test("chat composer only applies session-disabled skill subtraction to the backend catalog", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "ChatAppContent.tsx"),
    "utf8",
  );

  assert.match(source, /disabledSkillNames:\s*sessionConfig\.disabledSkills/);
  assert.doesNotMatch(source, /enabledSkillNames:/);
});
