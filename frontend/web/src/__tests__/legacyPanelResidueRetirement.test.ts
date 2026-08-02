import assert from "node:assert/strict";
import {
  existsSync,
  readFileSync,
  readdirSync,
} from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();
const locales = ["en", "zh", "ja", "ko", "ru"] as const;

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
    if (locale === "en" || locale === "zh") {
      assert.deepEqual(actual.mcpCard, [...retainedKeys.mcpCard], locale);
    } else {
      const missingFallbackKeys = retainedKeys.mcpCard.filter(
        (key) => !actual.mcpCard.includes(key),
      );
      assert.deepEqual(
        missingFallbackKeys,
        ["roleQuotaCount", "statusDisabled", "statusEnabled", "statusLabel"],
        locale,
      );
    }
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
