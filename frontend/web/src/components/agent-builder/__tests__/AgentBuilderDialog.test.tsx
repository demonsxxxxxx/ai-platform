import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

test("AgentBuilderDialog keeps a stable close callback and opening-only focus lifecycle", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/agent-builder/AgentBuilderDialog.tsx"),
    "utf8",
  );

  assert.match(source, /const onCloseRef = useRef\(onClose\)/);
  assert.match(source, /onCloseRef\.current = onClose/);
  assert.match(source, /\}, \[isOpen\]\);/);
  assert.match(source, /if \(!focusCancelled\) closeRef\.current\?\.focus\(\)/);
  assert.match(source, /onCloseRef\.current\(\)/);
});
