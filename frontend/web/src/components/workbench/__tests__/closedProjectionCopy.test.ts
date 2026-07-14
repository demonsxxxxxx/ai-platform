import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("post-login projection pages no longer cite closed backend gap issues", () => {
  const sources = [
    read("src/components/fileLibrary/RevealedFilesWorkbenchPanel.tsx"),
    read("src/i18n/locales/zh.json"),
    read("src/i18n/locales/en.json"),
  ].join("\n");

  assert.doesNotMatch(sources, /#229|#233|backend issue #229|后端 issue #229/i);
  assert.doesNotMatch(
    sources,
    /等待 revealed files 投影|Waiting for revealed files projection/,
  );
  assert.doesNotMatch(
    sources,
    /接口缺失|缺路由|missing contract|backend contract is missing|后端合同缺失/i,
  );
  assert.match(sources, /投影暂时不可用|projection is temporarily unavailable/i);
  assert.match(sources, /运行态可用性信号|runtime availability signal/i);
});
