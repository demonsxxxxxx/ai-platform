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
  assert.match(source, /isAiAdminUser\(user\)/);
  assert.match(source, /AvailableSkillsPanel/);

  const ordinarySkills = readFileSync(
    join(root, "src/components/panels/AvailableSkillsPanel.tsx"),
    "utf8",
  );
  assert.match(ordinarySkills, /data-ordinary-skills-catalog/);
  assert.match(ordinarySkills, /useSkills\(\{ allAuthorizedCatalog: true \}\)/);
  assert.match(ordinarySkills, /skills\.available\.title/);
  assert.match(ordinarySkills, /skills\.available\.fileTypes/);
  assert.doesNotMatch(
    ordinarySkills,
    /expected_version|file_count|skill\.content|skill\.files|is_published|marketplace_is_active/,
  );
});

test("mcp panel gives AI admins lifecycle controls while keeping the ordinary directory redacted", () => {
  const source = readFileSync(
    join(root, "src/components/panels/MCPPanel.tsx"),
    "utf8",
  );
  const form = readFileSync(
    join(root, "src/components/mcp/MCPServerForm.tsx"),
    "utf8",
  );
  const ordinaryCatalog = readFileSync(
    join(root, "src/components/panels/OrdinaryMcpCatalog.tsx"),
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
    /MCPServerCard|EditorSidebar/,
  );
  assert.match(source, /MCPServerForm/);
  assert.match(source, /ConfirmDialog/);
  assert.match(source, /canManageMcp && !mcpGovernance\.governedUnavailable/);
  assert.match(source, /data-mcp-admin-controls/);
  assert.match(source, /if \(!isAiAdmin\)/);
  assert.match(source, /allAuthorizedCatalog: !isAiAdmin/);
  assert.match(source, /OrdinaryMcpCatalog/);
  assert.match(source, /data-mcp-summary-status/);
  assert.equal(
    (source.match(/<GovernanceAvailabilityBadge/g) ?? []).length,
    1,
    "MCP panel keeps one authoritative status badge",
  );
  assert.match(source, /mcp\.admin\.directorySummary/);
  assert.match(source, /mcp\.admin\.permissionSummary/);
  assert.match(source, /mcp\.admin\.lifecycleSummary/);
  assert.match(source, /mcp\.admin\.credentialsSummary/);
  assert.match(source, /mcp\.card\.statusLabel/);
  assert.match(source, /mcp\.card\.statusEnabled/);
  assert.match(source, /mcp\.card\.statusDisabled/);
  assert.doesNotMatch(source, /t\("governance\.enabled"\)/);
  assert.doesNotMatch(source, /t\("governance\.disabled"\)/);
  assert.match(source, /createServer|updateServer|deleteServer|toggleServer/);
  assert.doesNotMatch(source, /importServers|exportServers|promoteServer|demoteServer/);
  assert.doesNotMatch(
    source,
    /startServer|stopServer|restartServer|rawCredential|allowedTransports|createAsSystem|changeToSystem/,
  );
  assert.doesNotMatch(form, /server\?\.url|server\.url/);
  assert.doesNotMatch(form, /server\?\.headers|server\.headers/);
  assert.doesNotMatch(form, /server\?\.command|server\.command/);
  assert.doesNotMatch(form, /server\?\.env_keys|server\.env_keys/);
  assert.match(form, /department_ids/);
  assert.match(form, /const \[allowedDepartmentsInput, setAllowedDepartmentsInput\] = useState/);
  assert.match(
    form,
    /department_ids: isSystemServer[\s\S]*?parseDepartmentIds\(allowedDepartmentsInput\)/,
  );
  assert.match(form, /value=\{allowedDepartmentsInput\}/);
  assert.match(form, /<label htmlFor="mcp-allowed-departments"/);
  assert.match(form, /id="mcp-allowed-departments"/);
  assert.match(
    form,
    /onChange=\{\(event\) => setAllowedDepartmentsInput\(event\.target\.value\)\}/,
  );
  assert.doesNotMatch(form, /value=\{allowedDepartments\.join/);
  assert.match(form, /if \(server\) \{[\s\S]*setUrl\(""\);[\s\S]*setHeaders\(\[\]\);[\s\S]*setCommand\(""\);[\s\S]*setEnvKeys\(\[\]\);/);
  assert.match(form, /else \{[\s\S]*setAllowedDepartmentsInput\(""\);/);
  assert.ok(
    form.indexOf('t("mcp.form.connectionReentry")') <
      form.indexOf("{/* ── Sandbox-specific fields ── */}"),
    "write-only re-entry warning must apply to every transport",
  );
  assert.match(ordinaryCatalog, /data-ordinary-mcp-catalog/);
  assert.match(ordinaryCatalog, /mcp\.available\.empty/);
  assert.match(ordinaryCatalog, /mcpApi\.discoverTools/);
  assert.doesNotMatch(
    ordinaryCatalog,
    /allowed_roles|role_quotas|credential|transport|server\.enabled|can_edit/,
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
  assert.equal(typeof locale("en").mcp.form.removeRole, "string");
  assert.equal(typeof locale("zh").mcp.form.removeRole, "string");
  assert.equal(locale("zh").mcp.available.empty, "暂无可用工具");
  assert.equal(locale("en").mcp.available.empty, "No tools available");
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
