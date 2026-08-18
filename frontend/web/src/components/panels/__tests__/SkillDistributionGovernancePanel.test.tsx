import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { renderToStaticMarkup } from "react-dom/server";

import "../../../i18n/index.ts";
import { DepartmentDirectorySelector } from "../DepartmentDirectorySelector.tsx";
import { buildControlledSkillDistributionUpdate } from "../skillDistributionDraft.ts";

test("Skill distribution editor uses server-backed ACL controls and safe errors", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillDistributionGovernancePanel.tsx"),
    "utf8",
  );

  assert.match(source, /capabilityDistributionApi\.list\("skill"\)/);
  assert.match(source, /capabilityDistributionApi\.departmentDirectory\(\)/);
  assert.match(source, /capabilityDistributionApi\.update\(/);
  assert.match(source, /data-skill-distribution-visible/);
  assert.match(source, /data-skill-distribution-status/);
  assert.match(source, /<DepartmentDirectorySelector/);
  assert.match(source, /resolveDepartmentSelection/);
  assert.doesNotMatch(source, /departmentInput|parseDepartmentIds/);
  assert.match(source, /<RoleSelector/);
  assert.match(source, /skills\.governance\.description/);
  assert.doesNotMatch(source, /Admin release|MCP 权限编辑器|stable 版本/);
  assert.doesNotMatch(source, /[\u4e00-\u9fff]/);
  assert.doesNotMatch(source, /error instanceof Error \? error\.message/);
});

test("Chinese Skill access translations stay complete", () => {
  const requiredPaths = [
    "departmentSelector.directory",
    "departmentSelector.remove",
    "departmentSelector.removeUnresolved",
    "departmentSelector.searchPlaceholder",
    "departmentSelector.unresolvedWarning",
    "departments.all",
    "departments.allowed",
    "departments.hint",
    "departments.restricted",
    "departments.scope",
    "description",
    "errors.conflict",
    "errors.directoryNotAuthoritative",
    "errors.directoryUnavailable",
    "errors.forbidden",
    "errors.notFound",
    "errors.saveFailed",
    "loading",
    "refresh",
    "release.current",
    "release.latest",
    "release.none",
    "roles.allowed",
    "roles.hint",
    "save",
    "saved",
    "selectPrompt",
    "status.active",
    "status.disabled",
    "title",
    "visibility.activeHidden",
    "visibility.activeVisible",
    "visibility.departmentCount",
    "visibleToUsers",
  ];

  const locale = "zh";
  const catalog = JSON.parse(
    readFileSync(join(process.cwd(), "src/i18n/locales/zh.json"), "utf8"),
  ) as { skills?: { governance?: Record<string, unknown> } };
  const governance = catalog.skills?.governance;
  assert.ok(governance, "Chinese Skill access translations must exist");
  for (const path of requiredPaths) {
    let value: unknown = governance;
    for (const key of path.split(".")) {
      value =
        value && typeof value === "object"
          ? (value as Record<string, unknown>)[key]
          : undefined;
    }
    assert.equal(typeof value, "string", `${locale}.skills.governance.${path}`);
  }
});

test("department selector is keyboard navigable and keeps unresolved values removable", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/DepartmentDirectorySelector.tsx"),
    "utf8",
  );

  assert.match(source, /data-skill-distribution-departments/);
  assert.match(source, /resolution\.unresolvedAuthorityIds\.map/);
  assert.match(source, /aria-multiselectable="true"/);
  assert.match(source, /aria-selected=\{checked\}/);
  assert.match(source, /event\.key === "ArrowDown"/);
  assert.match(source, /event\.key === "Escape"/);
  assert.match(source, /event\.key === "Home"/);
  assert.match(source, /MAX_SELECTED_DEPARTMENTS = 128/);
  assert.match(source, /aria-disabled=\{unavailable\}/);
  assert.match(source, /skills\.governance\.departmentSelector/);
  assert.doesNotMatch(source, /[\u4e00-\u9fff]/);
  assert.doesNotMatch(source, /split\(","\)|trim\(\).*department/);
});

test("non-empty unverified department scopes cannot reach the writer", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillDistributionGovernancePanel.tsx"),
    "utf8",
  );

  assert.match(source, /!departmentSelection\.authoritative/);
  assert.match(source, /directory === null/);
  const draftSource = readFileSync(
    join(process.cwd(), "src/components/panels/skillDistributionDraft.ts"),
    "utf8",
  );
  assert.match(draftSource, /\[\.\.\.draft\.departmentIds\]/);
  assert.match(source, /Promise\.allSettled/);
  assert.match(source, /setDirectory\(null\)/);
  assert.match(source, /directory === null/);
  assert.match(source, /departmentScope === "restricted"/);
});

test("controlled selected-Skill editor sends the exact all/restricted payload", () => {
  const draft = {
    status: "disabled" as const,
    visibleToUser: false,
    scopeMode: "allowlist" as const,
    departmentIds: ["工程部", "质量部"],
    allowedRoles: ["reviewer"],
    metadata: { source: "admin" },
  };

  assert.deepEqual(buildControlledSkillDistributionUpdate(draft, "restricted"), draft);
  assert.deepEqual(buildControlledSkillDistributionUpdate(draft, "all"), {
    ...draft,
    departmentIds: [],
  });

  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SkillDistributionGovernancePanel.tsx"),
    "utf8",
  );
  assert.match(source, /selectedSkillId: string \| null/);
  assert.doesNotMatch(source, /adminListSkills|setSelectedSkillId|setSkills\(/);
  assert.match(source, /data-skill-distribution-department-mode/);
  assert.match(source, /data-skill-distribution-status/);
  assert.match(source, /data-skill-distribution-visible/);
  assert.match(source, /<RoleSelector/);
});

test("department selector renders authoritative multiselect semantics", () => {
  const markup = renderToStaticMarkup(
    <DepartmentDirectorySelector
      directory={[
        {
          authorityId: "总部",
          children: [],
          directoryId: "1",
          name: "总部",
          path: "总部",
          reason: null,
          selectable: true,
        },
      ]}
      loadError={null}
      onChange={() => undefined}
      selectedAuthorityIds={["总部"]}
    />,
  );

  assert.match(markup, /aria-haspopup="listbox"/);
  assert.match(markup, /aria-describedby=/);
  assert.match(markup, /移除 总部/);
  assert.doesNotMatch(markup, /未确认部门会保留显示/);
});

test("department selector exposes unresolved values while directory authority is unavailable", () => {
  const markup = renderToStaticMarkup(
    <DepartmentDirectorySelector
      directory={null}
      loadError="权威部门目录暂时不可用"
      onChange={() => undefined}
      selectedAuthorityIds={["历史部门"]}
    />,
  );

  assert.match(markup, /移除未确认部门 历史部门/);
  assert.match(markup, /未确认部门会保留显示/);
  assert.match(markup, /权威部门目录暂时不可用/);
  assert.match(markup, /disabled=""/);
});
