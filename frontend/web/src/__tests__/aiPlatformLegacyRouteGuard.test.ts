import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

const legacyRouteChecks: Array<{
  relativePath: string;
  bannedPatterns: RegExp[];
}> = [
  {
    relativePath: "../pwaRouting.ts",
    bannedPatterns: [/["'`]\/ws["'`]/, /["'`]\/human["'`]/, /["'`]\/tools["'`]/],
  },
  {
    relativePath: "../hooks/useWebSocket.ts",
    bannedPatterns: [/new WebSocket\(/, /\/ws\b/],
  },
  {
    relativePath: "../hooks/useApprovals.ts",
    bannedPatterns: [/\/human(?:\/|`|["'])/],
  },
  {
    relativePath: "../hooks/useAgent/eventHandlers.ts",
    bannedPatterns: [/\/human\/\$\{/],
  },
  {
    relativePath: "../hooks/useAgent/historyLoader.ts",
    bannedPatterns: [/\/human\/\$\{/],
  },
  {
    relativePath: "../components/panels/ApprovalPanel.tsx",
    bannedPatterns: [/\/human\/\$\{/],
  },
  {
    relativePath: "../hooks/useTools.ts",
    bannedPatterns: [/\$\{API_BASE\}\/tools/],
  },
  {
    relativePath: "../components/profile/tabs/ProfileToolsTab.tsx",
    bannedPatterns: [/\$\{API_BASE\}\/tools/],
  },
];

test("active ai-platform source does not contain legacy LambChat runtime routes", () => {
  const violations: string[] = [];

  for (const check of legacyRouteChecks) {
    const source = readSource(check.relativePath);
    for (const pattern of check.bannedPatterns) {
      if (pattern.test(source)) {
        violations.push(`${check.relativePath}: ${pattern}`);
      }
    }
  }

  assert.deepEqual(violations, []);
});

test("profile modal does not expose the legacy env-var surface", () => {
  const profileModalSource = readSource("../components/profile/ProfileModal.tsx");

  assert.doesNotMatch(profileModalSource, /ProfileEnvVarsTab/);
  assert.doesNotMatch(profileModalSource, /envvars/);
  assert.doesNotMatch(profileModalSource, /envVars\.title/);
});

test("MCP tool endpoints remain allowed through ai-platform /api/mcp routes", () => {
  const useToolsSource = readSource("../hooks/useTools.ts");
  const profileToolsSource = readSource(
    "../components/profile/tabs/ProfileToolsTab.tsx",
  );

  assert.match(useToolsSource, /\$\{API_BASE\}\/mcp\/\$\{/);
  assert.match(profileToolsSource, /\$\{API_BASE\}\/mcp\/\$\{/);
});
