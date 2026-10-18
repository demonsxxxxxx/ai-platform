import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("unused common card and video modules and their exports stay retired", () => {
  const root = join(process.cwd(), "src");
  const retired = /SkillBaseCard|VideoViewer|getCategoryIcon/;
  for (const name of ["SkillBaseCard", "VideoViewer"]) {
    assert.equal(existsSync(join(root, "components/common", name + ".tsx")), false);
  }
  assert.equal(existsSync(join(root, "components/common/cardUtils.ts")), false);
  // Include dev sources and every barrel, not only direct component consumers.
  function inspect(directory: string) {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      if (entry.name === "__tests__") continue;
      const path = join(directory, entry.name);
      if (entry.isDirectory()) inspect(path);
      else if (/\.[cm]?[jt]sx?$/.test(path) && !/\.test\./.test(path)) {
        assert.doesNotMatch(readFileSync(path, "utf8"), retired, path);
      }
    }
  }
  inspect(root);
  assert.match(readFileSync(join(root, "components/common/index.ts"), "utf8"), /export \{ ImageViewer \}/);
});
