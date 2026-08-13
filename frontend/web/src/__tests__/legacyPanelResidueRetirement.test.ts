import assert from "node:assert/strict";
import {
  existsSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const locales = ["zh"] as const;
const memoryRootKeys = {
  zh: ["title", "workbench"],
} as const;

const retainedKeys = {
  users: ["user"],
  feedback: [
    "alreadySubmitted",
    "commentPlaceholder",
    "negative",
    "positive",
    "pressEnter",
    "submit",
    "submitFailed",
    "submitSuccess",
  ],
  notification: [
    "dismiss",
    "noNotifications",
    "taskCompleted",
    "taskFailed",
    "typeInfo",
    "typeMaintenance",
    "typeSuccess",
    "typeWarning",
  ],
  mcpCard: [
    "noTools",
    "roleCount",
    "roleQuotaCount",
    "statusDisabled",
    "statusEnabled",
    "statusLabel",
    "system",
    "user",
  ],
} as const;

const absentNamespaces = [
  "systemHealth",
  "settings",
  "categories",
  "subcategories",
] as const;

function source(relativePath: string): string {
  return readFileSync(join(root, relativePath), "utf8");
}

function sortedKeys(value: Record<string, unknown>): string[] {
  return Object.keys(value).sort();
}

function productionSourceFiles(directory: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name === "__tests__") continue;
    const entryPath = join(directory, entry.name);
    if (entry.isDirectory()) {
      files.push(...productionSourceFiles(entryPath));
      continue;
    }
    if (!entry.isFile()) continue;
    if (!/\.(?:[cm]?[jt]sx?)$/.test(entry.name)) continue;
    if (/\.(?:test|spec)\./.test(entry.name)) continue;
    files.push(entryPath);
  }
  return files;
}

function assertSubset(
  actual: string[],
  allowed: readonly string[],
  label: string,
): void {
  assert.deepEqual(
    actual.filter((key) => !allowed.includes(key)),
    [],
    label,
  );
}

test("retired legacy panel clients and barrel exports are absent", () => {
  const barrel = source("src/services/api.ts");
  for (const client of ["user", "settings", "health", "notification"]) {
    assert.equal(
      existsSync(join(root, `src/services/api/${client}.ts`)),
      false,
      client,
    );
  }
  for (const symbol of ["userApi", "settingsApi", "healthApi"]) {
    assert.doesNotMatch(barrel, new RegExp(`\\b${symbol}\\b`), symbol);
  }

  for (const activeClient of [
    "src/services/api/notificationPublic.ts",
    "src/services/api/workbench.ts",
    "src/services/api/feedback.ts",
  ]) {
    assert.equal(existsSync(join(root, activeClient)), true, activeClient);
  }
});

test("retired legacy memory service is absent while ai-platform memory APIs remain", () => {
  const memoryService = source("src/services/api/memory.ts");
  const barrel = source("src/services/api.ts");

  assert.doesNotMatch(memoryService, /\bmemoryApi\b/);
  assert.doesNotMatch(memoryService, /\bMemoryItem\b/);
  assert.doesNotMatch(memoryService, /\/api\/memory(?:\/|\?|["'`])/);
  assert.doesNotMatch(barrel, /\bmemoryApi\b/);

  for (const endpoint of [
    "/api/ai/memory/policy",
    "/api/ai/memory/records",
    "/api/ai/admin/memory/policies",
    "/api/ai/admin/memory/records",
    "/api/ai/admin/memory/retention/cleanup",
  ]) {
    assert.equal(memoryService.includes(endpoint), true, endpoint);
  }

  for (const api of [
    "buildMemoryPolicyUrl",
    "buildMemoryRecordsUrl",
    "buildAdminMemoryPoliciesUrl",
    "buildAdminMemoryRecordsUrl",
    "buildCleanupExpiredMemoryUrl",
    "normalizeMemoryRecord",
    "fetchMemoryPolicy",
    "setMemoryPolicy",
    "fetchAdminMemoryPolicies",
    "fetchMemoryRecords",
    "fetchAdminMemoryRecords",
    "deleteMemoryRecord",
    "cleanupExpiredMemoryRecords",
  ]) {
    assert.match(
      memoryService,
      new RegExp(`export (?:async )?function ${api}\\b`),
      api,
    );
  }
});

test("memory locale namespaces keep governed roots and cover active consumers", () => {
  const memoryPanel = source("src/components/panels/MemoryPanel/index.tsx");
  const consumedWorkbenchKeys = [
    ...new Set(
      Array.from(
        memoryPanel.matchAll(/["'`]memory\.workbench\.([A-Za-z0-9_-]+)["'`]/g),
        (match) => match[1],
      ),
    ),
  ].sort();

  assert.ok(consumedWorkbenchKeys.length > 0, "memory.workbench consumers");
  assert.ok(consumedWorkbenchKeys.includes("source"), "memory.workbench.source");

  for (const locale of locales) {
    const messages = JSON.parse(
      source(`src/i18n/locales/${locale}.json`),
    ) as {
      common: Record<string, unknown> & { timeAgo: Record<string, unknown> };
      memory: Record<string, unknown>;
      seo: { memory: Record<string, unknown> };
    };

    assert.deepEqual(
      sortedKeys(messages.memory),
      [...memoryRootKeys[locale]],
      `${locale}:memory`,
    );
    assert.equal(typeof messages.memory.title, "string", `${locale}:memory.title`);

    const workbench = messages.memory.workbench;
    assert.equal(
      typeof workbench === "object" && workbench !== null && !Array.isArray(workbench),
      true,
      `${locale}:memory.workbench`,
    );
    for (const key of consumedWorkbenchKeys) {
      assert.equal(
        Object.hasOwn(workbench as Record<string, unknown>, key),
        true,
        `${locale}:memory.workbench.${key}`,
      );
    }
    assert.equal(
      typeof (workbench as Record<string, unknown>).source,
      "string",
      `${locale}:memory.workbench.source`,
    );

    for (const key of ["description", "title"]) {
      const value = messages.seo.memory[key];
      assert.equal(typeof value, "string", `${locale}:seo.memory.${key}`);
      assert.notEqual(value, "", `${locale}:seo.memory.${key}`);
    }
    for (const key of ["daysAgo", "hoursAgo", "justNow", "minutesAgo", "monthsAgo"]) {
      const value = messages.common.timeAgo[key];
      assert.equal(typeof value, "string", `${locale}:common.timeAgo.${key}`);
      assert.notEqual(value, "", `${locale}:common.timeAgo.${key}`);
    }
    for (const key of ["timeMonthsAgo", "timeWeeksAgo"]) {
      const value = messages.common[key];
      assert.equal(typeof value, "string", `${locale}:common.${key}`);
      assert.notEqual(value, "", `${locale}:common.${key}`);
    }
  }
});

test("legacy panel locale residue keeps only audited active keys", () => {
  for (const locale of locales) {
    const messages = JSON.parse(
      source(`src/i18n/locales/${locale}.json`),
    ) as Record<string, unknown> & {
      users: Record<string, unknown>;
      feedback: Record<string, unknown>;
      notification: Record<string, unknown>;
      seo: Record<string, unknown>;
      mcp: { card: Record<string, unknown> };
    };

    for (const namespace of absentNamespaces) {
      assert.equal(Object.hasOwn(messages, namespace), false, `${locale}:${namespace}`);
    }
    assert.equal(Object.hasOwn(messages.seo, "landing"), false, `${locale}:seo.landing`);

    const actual = {
      users: sortedKeys(messages.users),
      feedback: sortedKeys(messages.feedback),
      notification: sortedKeys(messages.notification),
      mcpCard: sortedKeys(messages.mcp.card),
    };
    for (const namespace of Object.keys(retainedKeys) as Array<
      keyof typeof retainedKeys
    >) {
      assertSubset(
        actual[namespace],
        retainedKeys[namespace],
        `${locale}:${namespace}`,
      );
    }

    assert.deepEqual(actual.users, [...retainedKeys.users], locale);
    assert.deepEqual(actual.feedback, [...retainedKeys.feedback], locale);
    assert.deepEqual(actual.notification, [...retainedKeys.notification], locale);
    assert.deepEqual(actual.mcpCard, [...retainedKeys.mcpCard], locale);
  }
});

test("production source consumes only retained keys under audited namespaces", () => {
  const sourceText = productionSourceFiles(join(root, "src"))
    .map((file) => readFileSync(file, "utf8"))
    .join("\n");
  const expected = new Set([
    ...retainedKeys.users.map((key) => `users.${key}`),
    ...retainedKeys.feedback.map((key) => `feedback.${key}`),
    ...retainedKeys.notification.map((key) => `notification.${key}`),
    ...retainedKeys.mcpCard.map((key) => `mcp.card.${key}`),
  ]);
  const literalPattern =
    /["'`](systemHealth|users|settings|feedback|notification|categories|subcategories|seo\.landing|mcp\.card)\.([A-Za-z0-9_.-]+)["'`]/g;
  const consumed = new Set<string>();
  for (const match of sourceText.matchAll(literalPattern)) {
    consumed.add(`${match[1]}.${match[2]}`);
  }

  assert.deepEqual(
    [...consumed].filter((key) => !expected.has(key)).sort(),
    [],
  );
  assert.deepEqual([...expected].filter((key) => !consumed.has(key)).sort(), []);
  assert.doesNotMatch(
    sourceText,
    /["'`](?:systemHealth|users|settings|feedback|notification|categories|subcategories|seo\.landing|mcp\.card)\.\$\{/,
  );
  assert.doesNotMatch(
    sourceText,
    /["'`](?:systemHealth|users|settings|feedback|notification|categories|subcategories|seo\.landing|mcp\.card)\.["'`]\s*\+/,
  );
});
