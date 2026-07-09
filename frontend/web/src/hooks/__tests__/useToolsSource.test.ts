import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

test("composer tool hook only registers MCP tools returned by the backend-visible server list", () => {
  const source = readFileSync(
    join(import.meta.dirname, "..", "useTools.ts"),
    "utf8",
  );

  assert.match(source, /const serverResponse = await mcpApi\.list\(\)/);
  assert.match(source, /\(serverResponse\.servers \?\? \[\]\)\.map/);
  assert.match(source, /await mcpApi\.discoverTools\(server\.name\)/);
  assert.match(
    source,
    /enabled: server\.enabled && !tool\.system_disabled && !tool\.user_disabled/,
  );
});
