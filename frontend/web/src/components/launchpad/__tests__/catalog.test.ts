import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";

import {
  filterLaunchpadGroups,
  getLaunchpadIconUrl,
  launchpadGroups,
  resolveLaunchpadDestination,
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

test("launchpad keeps the copied web catalog and AI application entries", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);

  assert.equal(launchpadGroups.length, 13);
  assert.equal(entries.length, 127);
  assert.equal(new Set(entries.map((entry) => entry.id)).size, 127);
  assert.equal(
    new Set(entries.flatMap((entry) => (entry.icon ? [entry.icon] : []))).size,
    89,
  );
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

  assert.equal(launchpadGroups[0]?.entries[0]?.name, "灵犀平台");
  assert.deepEqual(
    entries.find((entry) => entry.name === "灵犀平台"),
    {
      id: "内网登录:灵犀平台",
      name: "灵犀平台",
      description: "公司自研平台",
      icon: "lingxi-platform.png",
      url: "http://10.56.0.25:8189/#/TaskManagement/indexSpace",
    },
  );
  assert.ok(entries.some((entry) => entry.name === "SOP问询助手"));
  assert.ok(entries.some((entry) => entry.name === "Word文档翻译"));
  assert.ok(entries.some((entry) => entry.name === "Word文档审核"));
});

test("AI applications keep internal routes and direct external URLs", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);
  const sopAssistant = entries.find((entry) => entry.name === "SOP问询助手");
  const wordTranslate = entries.find((entry) => entry.name === "Word文档翻译");
  const wordReview = entries.find((entry) => entry.name === "Word文档审核");
  const aiDraw = entries.find((entry) => entry.name === "ai-draw");
  const dataFormulator = entries.find((entry) => entry.name === "data-formulator");
  const pdfTranslate = entries.find((entry) => entry.name === "pdf-translate");

  assert.deepEqual(resolveLaunchpadDestination(sopAssistant!), {
    kind: "internal",
    path: "/ai-apps/sop-assistant",
  });
  assert.deepEqual(resolveLaunchpadDestination(wordReview!), {
    kind: "internal",
    path: "/ai-apps/word-review",
  });
  assert.deepEqual(resolveLaunchpadDestination(wordTranslate!), {
    kind: "url",
    href: "http://10.56.0.210:8000",
  });
  assert.deepEqual(resolveLaunchpadDestination(aiDraw!), {
    kind: "url",
    href: "http://10.56.1.57:3000/zh",
  });
  assert.deepEqual(resolveLaunchpadDestination(dataFormulator!), {
    kind: "url",
    href: "http://10.56.1.57:5567/",
  });
  assert.deepEqual(resolveLaunchpadDestination(pdfTranslate!), {
    kind: "url",
    href: "http://10.56.1.57:7860/",
  });
});

test("copied launchpad icons exist in the frontend public directory", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);

  for (const entry of entries) {
    if (entry.icon) {
      assert.ok(
        existsSync(join(process.cwd(), "public", "launchpad-icons", entry.icon)),
        `missing copied icon for ${entry.name}: ${entry.icon}`,
      );
    }
  }
  assert.equal(
    getLaunchpadIconUrl("满意度调研.jpg"),
    "/launchpad-icons/%E6%BB%A1%E6%84%8F%E5%BA%A6%E8%B0%83%E7%A0%94.jpg",
  );
});

test("launchpad catalog keeps destination metadata limited to current behavior", () => {
  for (const key of ["tab", "systemKey", "color"]) {
    assert.deepEqual(findCatalogMetadataKeyPaths(launchpadGroups, key), []);
  }
  assert.equal(findCatalogMetadataKeyPaths(launchpadGroups, "runtimeUrlKey").length, 0);
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
  for (const entry of launchpadGroups
    .flatMap((group) => group.entries)
    .filter((entry) => !entry.internalPath)) {
    assert.match(entry.url || "", /^https?:\/\//);
    assert.match(entry.icon || "", /\.(?:png|jpe?g|svg)$/i);
  }
});
