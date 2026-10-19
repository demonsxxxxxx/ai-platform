import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      return ["__tests__", "generated"].includes(entry.name) ? [] : sourceFiles(path);
    }
    return path.endsWith(".tsx") ? [path] : [];
  });
}

const retiredModules = [
  "components/common/EnterpriseSelect.tsx",
  "components/common/Checkbox.tsx",
  "components/common/Table.tsx",
  "components/common/Pagination.tsx",
  "components/common/LoadingSpinner.tsx",
  "components/skill/Toggle.tsx",
];

test("retired UI modules, test filename and direct dependencies stay absent", () => {
  const root = join(process.cwd(), "src");
  for (const path of retiredModules) {
    assert.equal(existsSync(join(root, path)), false, path);
  }
  assert.equal(existsSync(join(root, "components/common/__tests__/EnterpriseSelect.mounted.test.tsx")), false);
  assert.equal(existsSync(join(root, "components/common/__tests__/LobeSelectField.mounted.test.tsx")), true);
  const manifest = JSON.parse(readFileSync(join(process.cwd(), "package.json"), "utf8"));
  for (const dependency of ["@radix-ui/react-select", "react-hot-toast"]) {
    assert.equal(manifest.dependencies[dependency], undefined, dependency);
    assert.equal(manifest.devDependencies[dependency], undefined, dependency);
    assert.equal(manifest.optionalDependencies?.[dependency], undefined, dependency);
  }
  const controlsCss = readFileSync(join(root, "styles/components.css"), "utf8");
  assert.doesNotMatch(controlsCss, /pagination-(?:wrapper|controls|btn|page|ellipsis)|enterprise-select-|es-select-btn|btn-(?:icon|secondary|danger)|enterprise-(?:form-(?:input|textarea)|(?:danger-)?icon-button)/);
  assert.doesNotMatch(controlsCss, /editor-sidebar-close-btn/);
  assert.doesNotMatch(readFileSync(join(root, "styles/skill.css"), "utf8"), /@keyframes check-pop/);
});

test("active table/select consumers use Lobe UI and retain document-renderer semantics", () => {
  const root = join(process.cwd(), "src");
  const rendererTables = new Set([
    join(root, "components/chat/ChatMessage/MarkdownContent.tsx"),
    join(root, "components/documents/previews/ExcelPreview.tsx"),
  ]);
  for (const path of sourceFiles(root)) {
    const source = readFileSync(path, "utf8");
    assert.doesNotMatch(source, /<(?:select|option)\b/, path);
    assert.doesNotMatch(source, /\b(?:btn-(?:icon|secondary|danger)|enterprise-(?:form-(?:input|textarea)|(?:danger-)?icon-button))\b/, path);
    assert.doesNotMatch(source, /from\s+["'][^"']*\/common\/(?:EnterpriseSelect|Checkbox|Table|Pagination|LoadingSpinner)["']/, path);
    if (rendererTables.has(path)) {
      assert.match(source, /<th\b[^>]*scope="col"/, path);
    } else {
      assert.doesNotMatch(source, /<(?:table|thead|tbody|tr|th|td)\b/, path);
    }
  }
  for (const owner of [
    "components/panels/MCPPanel.tsx",
    "components/panels/ModelAdminControl.tsx",
    "components/panels/RunMonitorPanel.tsx",
    "components/panels/RunPerformanceSummary.tsx",
    "components/panels/SkillsPanel/SkillManagementTable.tsx",
    "components/selectors/ToolSelector.tsx",
    "features/agent-builder/AgentBuilderLifecycle.tsx",
  ]) {
    const source = readFileSync(join(root, owner), "utf8");
    assert.match(source, /import\s*\{[^}]*\bTable\b[^}]*\}\s*from\s*["']@lobehub\/ui\/base-ui["']/, owner);
    assert.match(source, /<Table\b/, owner);
    assert.match(source, /dataSource=\{/, owner);
    assert.match(source, /columns=\{/, owner);
  }
  assert.equal(existsSync(join(root, "components/documents/previews/MermaidDiagram.tsx")), false);
  assert.match(readFileSync(join(root, "components/documents/previews/MarkdownRenderer.tsx"), "utf8"), /MarkdownContent/);
});

test("retired department CSS stays absent while both owners retain the Lobe directory selector", () => {
  const root = join(process.cwd(), "src");
  const css = readFileSync(join(root, "styles/skill.css"), "utf8");
  assert.doesNotMatch(css, /\.department-selector(?:__[\w-]+)?(?![\w-])/);
  assert.match(css, /\.skill-authority-rail\b/);
  const selector = readFileSync(join(root, "components/panels/DepartmentDirectorySelector.tsx"), "utf8");
  assert.match(selector, /from "@lobehub\/ui\/base-ui"/);
  for (const control of ["SelectRoot", "SelectTrigger", "SelectPortal", "SelectPositioner", "SelectPopup", "SelectList", "SelectItem", "SelectItemIndicator"]) {
    assert.match(selector, new RegExp(`<${control}\\b`), control);
  }
  assert.match(selector, /data-department-directory-selector/);
  assert.match(selector, /data-department-directory-popup/);
  assert.doesNotMatch(selector, /department-selector/);
  for (const owner of [
    "components/panels/SkillDistributionGovernancePanel.tsx",
    "features/agent-builder/AgentBuilderEnterpriseFields.tsx",
  ]) {
    const source = readFileSync(join(root, owner), "utf8");
    assert.match(source, /<DepartmentDirectorySelector\b/, owner);
    assert.match(source, /directory=\{directory\}/, owner);
    assert.match(source, /loadError=\{directoryError\}/, owner);
    assert.match(source, /selectedAuthorityIds=\{(?:draft\.departmentIds|editor\.allowedDepartmentIds)\}/, owner);
  }
});
