import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { resolveGroupAvailability } from "../../governance/groupAvailability";

const root = process.cwd();
const shippedLocales = ["en", "zh", "ja", "ko", "ru"] as const;

function locale(localeName: (typeof shippedLocales)[number]) {
  return JSON.parse(
    readFileSync(
      join(root, `src/i18n/locales/${localeName}.json`),
      "utf8",
    ),
  );
}

test("group availability has explicit governed states", () => {
  assert.equal(resolveGroupAvailability({ enabled: true }).state, "enabled");
  assert.equal(resolveGroupAvailability({ enabled: false }).state, "disabled");
  assert.equal(resolveGroupAvailability({ inherited: true }).state, "inherited");
  assert.equal(resolveGroupAvailability({ backed: false }).state, "unavailable");
  assert.equal(resolveGroupAvailability({ adminOnly: true }).state, "admin-only");
});

test("skills hub exposes governed catalog status without composer help copy", () => {
  const source = readFileSync(
    join(root, "src/components/panels/SkillsHubPanel.tsx"),
    "utf8",
  );
  assert.match(source, /GovernanceAvailabilityBadge/);
  assert.match(source, /\$\{statusCopyNamespace\}\.\$\{statusCopyKey\}\.title/);
  assert.doesNotMatch(source, /skillsHub\.composerEntry/);
  assert.doesNotMatch(source, /data-skills-hub-composer-entry/);
  assert.match(source, /marketplace:\s*"\/marketplace"/);
  assert.match(source, /data-auth-projection-has-permission/);
  assert.match(source, /onCatalogStateChange/);
});

test("mcp panel exposes backed lifecycle governance without raw lifecycle controls", () => {
  const source = readFileSync(
    join(root, "src/components/panels/MCPPanel.tsx"),
    "utf8",
  );
  assert.match(source, /GovernanceAvailabilityBadge/);
  assert.match(source, /mcp\.permissionMode/);
  assert.match(source, /mcp\.lifecycleGovernance/);
  assert.match(source, /mcp\.credentialsGovernance/);
  assert.doesNotMatch(source, /mcp\.lifecycleUnavailable/);
  assert.doesNotMatch(source, /mcp\.credentialsUnavailable/);
  assert.match(source, /roleQuotaCount/);
  assert.doesNotMatch(source, /enabledToolCount/);
  assert.doesNotMatch(source, /mcp\.card\.tools/);
  assert.doesNotMatch(
    source,
    /MCPServerCard|MCPServerForm|ConfirmDialog|EditorSidebar/,
  );
  assert.doesNotMatch(
    source,
    /createServer|updateServer|deleteServer|toggleServer|importServers|exportServers|promoteServer|demoteServer/,
  );
  assert.doesNotMatch(
    source,
    /startServer|stopServer|restartServer|rawCredential|allowedTransports|createAsSystem|changeToSystem/,
  );
});

test("mcp governance copy exists across shipped workbench locales", () => {
  for (const localeName of shippedLocales) {
    const mcp = locale(localeName).mcp;
    assert.equal(
      typeof mcp.permissionMode,
      "string",
      `${localeName} missing mcp.permissionMode`,
    );
    assert.equal(
      typeof mcp.addToComposer,
      "string",
      `${localeName} missing mcp.addToComposer`,
    );
    assert.equal(
      typeof mcp.lifecycleGovernance?.title,
      "string",
      `${localeName} missing mcp.lifecycleGovernance.title`,
    );
    assert.equal(
      typeof mcp.lifecycleGovernance?.description,
      "string",
      `${localeName} missing mcp.lifecycleGovernance.description`,
    );
    assert.equal(
      typeof mcp.credentialsGovernance?.title,
      "string",
      `${localeName} missing mcp.credentialsGovernance.title`,
    );
    assert.equal(
      typeof mcp.credentialsGovernance?.description,
      "string",
      `${localeName} missing mcp.credentialsGovernance.description`,
    );
  }
});

test("share dialog fails closed until ai-platform share ACL projection exists", () => {
  const source = readFileSync(
    join(root, "src/components/share/ShareDialog.tsx"),
    "utf8",
  );
  assert.match(source, /ShareUnavailableState/);
  assert.match(source, /share\.unavailable\.unavailable/);
  assert.doesNotMatch(source, /shareApi\.create|listBySession|delete\(/);
  assert.doesNotMatch(source, /ShareVisibility|visibility|public|authenticated/);
});

test("tool selector cannot toggle system disabled MCP tools", () => {
  const source = readFileSync(
    join(root, "src/components/selectors/ToolSelector.tsx"),
    "utf8",
  );
  assert.match(source, /handleToolToggle/);
  assert.match(source, /if\s*\(tool\.system_disabled\)\s*return/);
  assert.match(source, /aria-disabled=\{tool\.system_disabled/);
  assert.doesNotMatch(source, /onClick=\{\(\) => onToggleTool\(tool\.name\)\}/);
});
