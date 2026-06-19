import assert from "node:assert/strict";
import test from "node:test";
import {
  loadPersistedSessionConfig,
  persistSessionConfig,
} from "../useSessionConfig.ts";

function installLocalStorageStub(initialEntries: Record<string, string>) {
  const originalLocalStorage = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const stored = new Map(Object.entries(initialEntries));

  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem(key: string) {
        return stored.get(key) ?? null;
      },
      removeItem(key: string) {
        stored.delete(key);
      },
      setItem(key: string, value: string) {
        stored.set(key, value);
      },
    },
  });

  return {
    stored,
    restore() {
      if (originalLocalStorage) {
        Object.defineProperty(globalThis, "localStorage", originalLocalStorage);
      } else {
        delete (globalThis as { localStorage?: Storage }).localStorage;
      }
    },
  };
}

test("migrates legacy session config storage key into the ai-platform namespace", () => {
  const legacyConfig = {
    disabledSkills: ["skill-a"],
    disabledMcpTools: ["tool-a"],
    personaPresetId: "persona-1",
    personaSnapshot: { name: "Persona" },
  };
  const stubs = installLocalStorageStub({
    lambchat_session_config: JSON.stringify(legacyConfig),
  });

  try {
    assert.deepEqual(loadPersistedSessionConfig(), legacyConfig);
    assert.equal(stubs.stored.has("lambchat_session_config"), false);
    assert.equal(
      stubs.stored.get("ai_platform_session_config"),
      JSON.stringify(legacyConfig),
    );
  } finally {
    stubs.restore();
  }
});

test("persists session config only under the ai-platform namespace", () => {
  const stubs = installLocalStorageStub({});
  const config = {
    disabledSkills: ["skill-b"],
    disabledMcpTools: [],
    personaPresetId: null,
    personaSnapshot: null,
  };

  try {
    persistSessionConfig(config);

    assert.equal(stubs.stored.has("lambchat_session_config"), false);
    assert.equal(
      stubs.stored.get("ai_platform_session_config"),
      JSON.stringify(config),
    );
  } finally {
    stubs.restore();
  }
});

test("drops corrupt legacy session config without copying it", () => {
  const stubs = installLocalStorageStub({
    lambchat_session_config: "{not-json",
  });

  try {
    assert.equal(loadPersistedSessionConfig(), null);
    assert.equal(stubs.stored.has("lambchat_session_config"), false);
    assert.equal(stubs.stored.has("ai_platform_session_config"), false);
  } finally {
    stubs.restore();
  }
});
