import assert from "node:assert/strict";
import test from "node:test";

import type { AdminSkillCatalogItem } from "../../../services/api/skill.ts";
import type { SkillResponse } from "../../../types/skill.ts";
import {
  buildSkillCatalogEntries,
  filterSkillCatalogEntries,
  removeArchivedActionSelections,
  resolveArchivedSkillCatalogEntries,
  resolveSkillCatalogMetrics,
  resolveSkillCatalogPage,
} from "../SkillsPanel/skillCatalogEntries.ts";

function runtimeSkill(name: string): SkillResponse {
  return {
    name,
    expected_version: "1.0.0",
    description: "Runtime description",
    tags: ["review"],
    enabled: true,
    source: "marketplace",
    files: {},
    file_count: 2,
    installed_from: "marketplace",
    is_published: true,
    marketplace_is_active: true,
  };
}

function adminSkill(
  skillId: string,
  name: string,
  overrides: Partial<AdminSkillCatalogItem> = {},
): AdminSkillCatalogItem {
  return {
    skillId,
    name,
    description: "Admin description",
    lifecycleStatus: "active",
    distributionStatus: "active",
    visibleToUser: true,
    latestVersion: "sha-1",
    latestVersionStatus: "released",
    currentVersion: "sha-1",
    rolloutPercent: 100,
    ...overrides,
  };
}

test("catalog entries keep opaque skill ids while preserving runtime action names", () => {
  const entries = buildSkillCatalogEntries(
    [runtimeSkill("qa-file-reviewer")],
    [adminSkill("skill-opaque-42", "qa-file-reviewer")],
  );

  assert.equal(entries.length, 1);
  assert.equal(entries[0]?.id, "skill-opaque-42");
  assert.equal(entries[0]?.actionName, "qa-file-reviewer");
  assert.equal(entries[0]?.adminSkill?.skillId, "skill-opaque-42");
  assert.equal(entries[0]?.runtimeSkill?.name, "qa-file-reviewer");
});

test("runtime delete results resolve to opaque catalog ids", () => {
  assert.deepEqual(
    resolveArchivedSkillCatalogEntries(
      [adminSkill("skill-opaque-42", "qa-file-reviewer")],
      ["qa-file-reviewer", "runtime-only"],
    ),
    [
      {
        id: "skill-opaque-42",
        actionName: "qa-file-reviewer",
        displayName: "qa-file-reviewer",
      },
      {
        id: "runtime-only",
        actionName: "runtime-only",
        displayName: "runtime-only",
      },
    ],
  );
});

test("single delete removes only the archived runtime action from batch selection", () => {
  assert.deepEqual(
    [...removeArchivedActionSelections(new Set(["skill-a", "skill-b"]), ["skill-a"])],
    ["skill-b"],
  );
});

test("admin draft records remain visible in the canonical list", () => {
  const entries = buildSkillCatalogEntries([], [
    adminSkill("draft-skill", "Draft Skill", {
      latestVersionStatus: "draft",
      currentVersion: null,
      rolloutPercent: null,
      visibleToUser: false,
    }),
  ]);

  assert.deepEqual(
    entries.map((entry) => ({
      id: entry.id,
      actionName: entry.actionName,
      status: entry.catalogStatus,
      runtimeEnabled: entry.runtimeEnabled,
    })),
    [
      {
        id: "draft-skill",
        actionName: null,
        status: "unpublished",
        runtimeEnabled: null,
      },
    ],
  );
  assert.equal(filterSkillCatalogEntries(entries, "draft", []).length, 1);
  assert.equal(filterSkillCatalogEntries(entries, "missing", []).length, 0);
});

test("a server-paginated catalog is not sliced for a second time", () => {
  const entries = buildSkillCatalogEntries(
    [runtimeSkill("skill-21"), runtimeSkill("skill-22")],
    [],
  );

  const page = resolveSkillCatalogPage({
    entries,
    page: 2,
    pageSize: 20,
    localPagination: false,
    serverTotal: 42,
  });

  assert.deepEqual(
    page.entries.map((entry) => entry.id),
    ["skill-21", "skill-22"],
  );
  assert.equal(page.total, 42);
});

test("the complete authorized catalog is paginated locally", () => {
  const entries = buildSkillCatalogEntries(
    Array.from({ length: 22 }, (_, index) => runtimeSkill(`skill-${index + 1}`)),
    [],
  );

  const page = resolveSkillCatalogPage({
    entries,
    page: 2,
    pageSize: 20,
    localPagination: true,
    serverTotal: 999,
  });

  assert.deepEqual(
    page.entries.map((entry) => entry.id),
    ["skill-21", "skill-22"],
  );
  assert.equal(page.total, 22);
});

test("management metrics stay catalog-wide when the visible list is filtered", () => {
  const entries = buildSkillCatalogEntries(
    [runtimeSkill("enabled-visible"), runtimeSkill("disabled-hidden")],
    [],
  );
  entries[1] = {
    ...entries[1]!,
    runtimeEnabled: false,
    catalogStatus: "hidden",
  };

  assert.equal(filterSkillCatalogEntries(entries, "enabled", []).length, 1);
  assert.deepEqual(resolveSkillCatalogMetrics(entries), {
    total: 2,
    enabled: 1,
    visible: 1,
  });
});
