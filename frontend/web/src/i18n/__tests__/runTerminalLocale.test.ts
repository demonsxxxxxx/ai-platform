import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

function locale() {
  return JSON.parse(readFileSync(join(process.cwd(), "src/i18n/locales/zh.json"), "utf8"));
}

test("run terminal result-unavailable copy is available in Chinese", () => {
  assert.equal(
    locale().chat.runTerminal.resultUnavailable,
    "任务终态已确认，但结果暂时无法加载。请刷新会话。",
  );
});
test("run terminal unavailable-status copy is available in Chinese", () => {
  assert.equal(
    locale().chat.runTerminal.statusUnavailable,
    "任务状态暂时无法同步。请刷新当前会话后重试。",
  );
});

test("run terminal turn-limit exhaustion copy is a required Chinese locale key", () => {
  const zh = locale().chat.runTerminal;

  assert.equal(typeof zh.runBudgetExhausted, "string");
  assert.equal(zh.runBudgetExhausted, "任务已达到执行轮次上限。请缩小或拆分任务后重试。");
});
