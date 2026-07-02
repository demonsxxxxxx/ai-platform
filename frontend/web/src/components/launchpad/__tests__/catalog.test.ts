import test from "node:test";
import assert from "node:assert/strict";

import {
  filterLaunchpadGroups,
  getLegacyWebUiFrameUrl,
  launchpadGroups,
  resolveLaunchpadDestination,
} from "../catalog.ts";

test("launchpad catalog contains the three copied navigation areas", () => {
  const tabCounts = new Map<string, number>();
  for (const group of launchpadGroups) {
    tabCounts.set(
      group.tab,
      (tabCounts.get(group.tab) ?? 0) + group.entries.length,
    );
  }

  assert.equal(tabCounts.get("lingxi"), 29);
  assert.equal(tabCounts.get("common"), 122);
  assert.equal(tabCounts.get("ai"), 4);
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

test("destination resolver opens urls and maps known nonGMPlims systems", () => {
  assert.equal(
    getLegacyWebUiFrameUrl(),
    "http://10.56.0.25:8189/#/TaskManagement/indexSpace/",
  );

  const wordTranslate = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.name === "Word文档翻译");
  assert.equal(resolveLaunchpadDestination(wordTranslate!)?.kind, "url");

  const sampleSender = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.systemKey === "SampleSender");
  assert.deepEqual(resolveLaunchpadDestination(sampleSender!), {
    kind: "url",
    href: "http://10.56.0.25:8189/#/RDSampleSender/dashboard/overview",
  });
});

test("destination resolver marks unknown nonGMPlims systems unavailable", () => {
  assert.deepEqual(
    resolveLaunchpadDestination({
      id: "unknown",
      tab: "lingxi",
      groupId: "test",
      groupName: "测试",
      name: "未映射系统",
      systemKey: "UnknownLegacySystem",
    }),
    {
      kind: "unavailable",
      reason: "待接入",
    },
  );
});
