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

  assert.equal(tabCounts.get("lingxi"), 31);
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

  const adDeviceManage = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.systemKey === "ADDeviceManage");
  assert.deepEqual(resolveLaunchpadDestination(adDeviceManage!), {
    kind: "url",
    href: "http://10.56.0.25:8189/#/ADDevice/overview",
  });

  const adLiquidPrep = launchpadGroups
    .flatMap((group) => group.entries)
    .find((entry) => entry.systemKey === "ADLiquidPrep");
  assert.deepEqual(resolveLaunchpadDestination(adLiquidPrep!), {
    kind: "url",
    href: "http://10.56.0.25:8189/#/ADLiquidPrep/print",
  });
});

test("lingxi catalog tracks the legacy webUI navigation labels", () => {
  const entries = launchpadGroups.flatMap((group) => group.entries);

  assert.ok(entries.some((entry) => entry.name === "AD设备管理"));
  assert.ok(entries.some((entry) => entry.name === "AD配液系统"));
  assert.ok(entries.some((entry) => entry.name === "商务运营管理系统"));
  assert.ok(!entries.some((entry) => entry.name === "合同管理系统"));
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
