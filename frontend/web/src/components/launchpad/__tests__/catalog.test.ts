import test from "node:test";
import assert from "node:assert/strict";

import {
  filterLaunchpadGroups,
  launchpadGroups,
  launchpadTabs,
  resolveLaunchpadDestination,
} from "../catalog.ts";

function findCatalogMetadataKeyPaths(value: unknown, key: string, path = "$"): string[] {
  if (!value || typeof value !== "object") {
    return [];
  }

  if (Array.isArray(value)) {
    return value.flatMap((item, index) =>
      findCatalogMetadataKeyPaths(item, key, `${path}[${index}]`),
    );
  }

  return Object.entries(value as Record<string, unknown>).flatMap(
    ([entryKey, entryValue]) => {
      const entryPath = `${path}.${entryKey}`;
      return [
        ...(entryKey === key ? [entryPath] : []),
        ...findCatalogMetadataKeyPaths(entryValue, key, entryPath),
      ];
    },
  );
}

test("launchpad catalog contains only company navigation and AI app entries", () => {
  const tabCounts = new Map<string, number>();
  for (const group of launchpadGroups) {
    tabCounts.set(
      group.tab,
      (tabCounts.get(group.tab) ?? 0) + group.entries.length,
    );
  }

  assert.equal(tabCounts.get("lingxi"), undefined);
  assert.equal(tabCounts.get("common"), 122);
  assert.equal(tabCounts.get("ai"), 4);
});

test("lingxi platform is an external jump instead of an embedded catalog category", () => {
  const lingxi = launchpadTabs.find((tab) => tab.key === "lingxi") as
    | { url?: string }
    | undefined;

  assert.equal(
    lingxi?.url,
    "http://10.56.0.25:8189/#/TaskManagement/indexSpace/",
  );
  assert.equal(launchpadGroups.some((group) => group.tab === "lingxi"), false);
});

test("launchpad catalog does not retain copied legacy icon or system metadata", () => {
  assert.deepEqual(findCatalogMetadataKeyPaths(launchpadGroups, "icon"), []);
  assert.deepEqual(findCatalogMetadataKeyPaths(launchpadGroups, "systemKey"), []);
});

test("search filters by app name, description, and group name", () => {
  const result = filterLaunchpadGroups(launchpadGroups, "SOP");

  assert.ok(result.some((group) => group.name === "知识库"));
  assert.ok(
    result
      .flatMap((group) => group.entries)
      .some((entry) => entry.name === "SOP问询助手"),
  );
});

test("destination resolver opens top-level urls and maps known nonGMPlims systems", () => {
  const wordTranslate = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.name === "Word文档翻译");
  assert.equal(resolveLaunchpadDestination(wordTranslate!)?.kind, "url");

  const wordReview = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.name === "Word文档审核");
  assert.deepEqual(resolveLaunchpadDestination(wordReview!), {
    kind: "url",
    href: "http://10.56.0.25:8189/#/AI/WordReview",
  });

  const ragflow = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.name === "SOP问询助手");
  assert.deepEqual(resolveLaunchpadDestination(ragflow!), {
    kind: "url",
    href: "http://10.56.0.25:8189/#/AI/RAGFlowSOP",
  });
});

test("lingxi application labels are no longer copied into the company catalog", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);

  assert.ok(!entries.some((entry) => entry.name === "AD设备管理"));
  assert.ok(!entries.some((entry) => entry.name === "AD配液系统"));
  assert.ok(!entries.some((entry) => entry.name === "商务运营管理系统"));
  assert.ok(!entries.some((entry) => entry.name === "合同管理系统"));
});

test("destination resolver marks entries without urls unavailable", () => {
  assert.deepEqual(
    resolveLaunchpadDestination({
      id: "unknown",
      tab: "common",
      groupId: "test",
      groupName: "测试",
      name: "未映射系统",
    }),
    {
      kind: "unavailable",
      reason: "待接入",
    },
  );
});
