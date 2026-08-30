import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const panelSource = readFileSync(
  join(process.cwd(), "src/components/panels/KnowledgePanel.tsx"),
  "utf8",
);
const featureSource = [
  "CatalogPrimitives.tsx",
  "ConnectionForms.tsx",
  "KnowledgeCatalogs.tsx",
  "SourceForms.tsx",
]
  .map((name) =>
    readFileSync(
      join(process.cwd(), "src/features/knowledge/components", name),
      "utf8",
    ),
  )
  .join("\n");
const source = `${panelSource}\n${featureSource}`;

test("Knowledge panel presents separate governed connection and source workflows", () => {
  assert.match(source, /RAGFlow 连接/);
  assert.match(source, /知识源与部门权限/);
  assert.match(source, /knowledgeApi\.checkConnection/);
  assert.match(source, /knowledgeApi\.activateConnection/);
  assert.match(source, /knowledgeApi\.syncConnection/);
  assert.match(source, /knowledgeApi\.replaceSourceAcl/);
  assert.match(source, /knowledgeApi\.rotateCredential/);
  assert.match(source, /最近认证/);
  assert.match(source, /最近完整同步/);
  assert.match(source, /同步连接/);
  assert.match(source, /display_name/);
  assert.match(source, /限定部门/);
  assert.doesNotMatch(source, /tenant_id|租户/);
});

test("Knowledge credential and private provider identity stay outside read projections", () => {
  assert.match(source, /type="password"/);
  assert.match(source, /autoComplete="new-password"/);
  assert.match(source, /仅本次写入，保存后不再回显/);
  assert.match(source, /credential_fingerprint/);
  assert.doesNotMatch(source, /provider_resource_id|dataset_id|provider_cursor/);
});

test("Knowledge panel has bounded pagination and nested viewport scrolling", () => {
  assert.match(panelSource, /connectionCursors/);
  assert.match(panelSource, /sourceCursors/);
  assert.match(panelSource, /sourceConnectionFilter/);
  assert.match(panelSource, /sourceStatusFilter/);
  assert.match(source, /上一页/);
  assert.match(source, /下一页/);
  assert.match(source, /workbenchSurface\.catalog\.content/);
  assert.match(source, /max-h-\[calc\(100dvh-2rem\)\]/);
  assert.match(source, /overflow-y-auto/);
  assert.match(source, /role="dialog"/);
  assert.match(source, /aria-modal="true"/);
  assert.match(panelSource, /role="alert"/);
  assert.match(panelSource, /role="status"/);
});
