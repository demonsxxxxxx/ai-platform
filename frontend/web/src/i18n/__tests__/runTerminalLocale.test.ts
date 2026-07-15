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
