import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

function locale(name: "en" | "zh") {
  return JSON.parse(readFileSync(join(process.cwd(), `src/i18n/locales/${name}.json`), "utf8"));
}

test("run terminal status retry exhaustion copy is available in Chinese and English", () => {
  assert.equal(
    locale("zh").chat.runTerminal.statusUnavailable,
    "任务状态暂时无法同步。请刷新当前会话后重试。",
  );
  assert.equal(
    locale("en").chat.runTerminal.statusUnavailable,
    "The task status could not be synchronized. Refresh this conversation and try again.",
  );
});

test("run terminal turn-limit exhaustion copy is a required Chinese and English locale key", () => {
  const zh = locale("zh").chat.runTerminal;
  const en = locale("en").chat.runTerminal;

  assert.equal(typeof zh.runBudgetExhausted, "string");
  assert.equal(typeof en.runBudgetExhausted, "string");
  assert.equal(zh.runBudgetExhausted, "任务已达到执行轮次上限。请缩小或拆分任务后重试。");
  assert.equal(
    en.runBudgetExhausted,
    "The task reached its execution-turn limit. Narrow or split the task, then retry.",
  );
});
