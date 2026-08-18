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

test("legacy admin rows defer user availability to the authorized runtime projection", () => {
  const [entry] = buildSkillCatalogEntries(
    [runtimeSkill("baoyu-translate")],
    [
      adminSkill("baoyu-translate", "baoyu-translate", {
        latestVersionStatus: "active",
        currentVersion: null,
        rolloutPercent: null,
      }),
    ],
  );

  assert.equal(entry?.catalogStatus, "available");
  assert.equal(entry?.runtimeEnabled, true);
});

test("an active admin-only seed is retained as an internal dependency", () => {
  const [entry] = buildSkillCatalogEntries([], [
    adminSkill("minimax-docx", "Minimax DOCX", {
      latestVersionStatus: "active",
      currentVersion: null,
      rolloutPercent: null,
      visibleToUser: false,
    }),
  ]);

  assert.equal(entry?.catalogStatus, "internal");
  assert.equal(entry?.actionName, null);
  assert.equal(entry?.runtimeEnabled, null);
});

test("an active user-visible seed without runtime authority stays unpublished", () => {
  const [entry] = buildSkillCatalogEntries([], [
    adminSkill("pending-runtime", "Pending Runtime", {
      latestVersionStatus: "active",
      currentVersion: null,
      rolloutPercent: null,
      visibleToUser: true,
    }),
  ]);

  assert.equal(entry?.catalogStatus, "unpublished");
});

test("catalog views separate available, internal, and restricted entries", () => {
  const entries = buildSkillCatalogEntries(
    [runtimeSkill("visible-skill")],
    [
      adminSkill("visible-skill", "visible-skill"),
      adminSkill("internal-skill", "Internal Skill", {
        latestVersionStatus: "active",
        currentVersion: null,
        visibleToUser: false,
      }),
      adminSkill("draft-skill", "Draft Skill", {
        latestVersionStatus: "draft",
        currentVersion: null,
        visibleToUser: false,
      }),
    ],
  );

  assert.deepEqual(
    filterSkillCatalogEntries(entries, "", [], "available").map(
      (entry) => entry.id,
    ),
    ["visible-skill"],
  );
  assert.deepEqual(
    filterSkillCatalogEntries(entries, "", [], "internal").map(
      (entry) => entry.id,
    ),
    ["internal-skill"],
  );
  assert.deepEqual(
    filterSkillCatalogEntries(entries, "", [], "restricted").map(
      (entry) => entry.id,
    ),
    ["draft-skill"],
  );
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

test("the current catalog size renders as two local pages by default", () => {
  const entries = buildSkillCatalogEntries(
    Array.from({ length: 14 }, (_, index) => runtimeSkill(`skill-${index + 1}`)),
    [],
  );

  const firstPage = resolveSkillCatalogPage({
    entries,
    page: 1,
    pageSize: 10,
    localPagination: true,
    serverTotal: 999,
  });
  const secondPage = resolveSkillCatalogPage({
    entries,
    page: 2,
    pageSize: 10,
    localPagination: true,
    serverTotal: 999,
  });

  assert.equal(firstPage.entries.length, 10);
  assert.equal(firstPage.page, 1);
  assert.equal(secondPage.entries.length, 4);
  assert.equal(secondPage.page, 2);
});

test("local pagination clamps stale pages after filtering", () => {
  const entries = buildSkillCatalogEntries(
    Array.from({ length: 3 }, (_, index) => runtimeSkill(`skill-${index + 1}`)),
    [],
  );

  const page = resolveSkillCatalogPage({
    entries,
    page: 4,
    pageSize: 10,
    localPagination: true,
    serverTotal: 999,
  });

  assert.equal(page.page, 1);
  assert.equal(page.entries.length, 3);
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
    internal: 0,
  });
});
