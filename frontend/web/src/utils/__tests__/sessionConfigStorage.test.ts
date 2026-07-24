import test from "node:test";
import assert from "node:assert/strict";
import {
  readSessionConfigStorage,
  SESSION_CONFIG_STORAGE_KEY,
  writeSessionConfigStorage,
} from "../sessionConfigStorage.ts";

test("legacy global MCP selections are never restored across sessions", () => {
  const values = new Map<string, string>();
  values.set(
    SESSION_CONFIG_STORAGE_KEY,
    JSON.stringify({
      disabledSkills: ["legacy-skill"],
      disabledMcpTools: ["private-server:search"],
      selectedMcpToolIds: ["tenant-search"],
    }),
  );
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    },
  });

  assert.equal(
    readSessionConfigStorage(),
    JSON.stringify({ disabledSkills: ["legacy-skill"] }),
  );

  writeSessionConfigStorage(
    JSON.stringify({
      disabledSkills: ["kept-skill"],
      selectedMcpToolIds: ["must-not-leak"],
    }),
  );
  assert.deepEqual(JSON.parse(values.get(SESSION_CONFIG_STORAGE_KEY) || "{}"), {
    disabledSkills: ["kept-skill"],
  });
});
