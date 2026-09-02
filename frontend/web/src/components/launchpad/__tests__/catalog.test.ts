import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";

import {
  filterLaunchpadGroups,
  getLaunchpadIconUrl,
  launchpadGroups,
} from "../catalog.ts";

function findCatalogMetadataKeyPaths(
  value: unknown,
  key: string,
  path = "$",
): string[] {
  if (!value || typeof value !== "object") return [];

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

test("launchpad contains only the copied web-navigation catalog", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);

  assert.equal(launchpadGroups.length, 13);
  assert.equal(entries.length, 121);
  assert.equal(new Set(entries.map((entry) => entry.id)).size, 121);
  assert.equal(new Set(entries.map((entry) => entry.icon)).size, 84);
  assert.deepEqual(
    launchpadGroups.map((group) => group.name),
    [
      "内网登录",
      "AI",
      "翻译",
      "绘图",
      "文献检索",
      "文献期刊",
      "专利检索",
      "药物蛋白数据库",
      "预测工具",
      "中国药监机构或协会",
      "国外药监机构或协会",
      "药典查询",
      "财经资讯",
    ],
  );

  assert.ok(entries.some((entry) => entry.name === "ai-platform"));
  assert.ok(!entries.some((entry) => entry.name === "公司规章制度"));
  assert.ok(!entries.some((entry) => entry.name === "SOP问询助手"));
  assert.ok(!entries.some((entry) => entry.name === "Word文档翻译"));
});

test("copied launchpad icons exist in the frontend public directory", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);

  for (const entry of entries) {
    assert.ok(
      existsSync(join(process.cwd(), "public", "launchpad-icons", entry.icon)),
      `missing copied icon for ${entry.name}: ${entry.icon}`,
    );
  }
  assert.equal(
    getLaunchpadIconUrl("满意度调研.jpg"),
    "/launchpad-icons/%E6%BB%A1%E6%84%8F%E5%BA%A6%E8%B0%83%E7%A0%94.jpg",
  );
});

test("launchpad catalog removes obsolete tab and runtime metadata", () => {
  for (const key of [
    "tab",
    "runtimeUrlKey",
    "unavailableReason",
    "systemKey",
    "color",
  ]) {
    assert.deepEqual(findCatalogMetadataKeyPaths(launchpadGroups, key), []);
  }
});

test("search filters by website name, description, and category", () => {
  const byName = filterLaunchpadGroups(launchpadGroups, "DeepSeek");
  assert.deepEqual(
    byName.flatMap((group) => group.entries).map((entry) => entry.name),
    ["DeepSeek"],
  );

  const byDescription = filterLaunchpadGroups(launchpadGroups, "共同编辑");
  assert.deepEqual(
    byDescription.flatMap((group) => group.entries).map((entry) => entry.name),
    ["vDrive(内部)", "vDrive(外部)"],
  );

  const byCategory = filterLaunchpadGroups(launchpadGroups, "药典查询");
  assert.equal(byCategory.length, 1);
  assert.equal(byCategory[0]?.entries.length, 3);
});

test("every copied web-navigation entry has a direct destination and icon", () => {
  for (const entry of launchpadGroups.flatMap((group) => group.entries)) {
    assert.match(entry.url, /^https?:\/\//);
    assert.match(entry.icon, /\.(?:png|jpe?g)$/i);
  }
});
