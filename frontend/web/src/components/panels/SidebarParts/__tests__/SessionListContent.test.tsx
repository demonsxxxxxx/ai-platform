import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import { createRequire } from "node:module";
import type { ReactElement, ReactNode } from "react";
import ts from "typescript";

import type { BackendSession } from "../../../../services/api.ts";
import { groupSessionsByAgent } from "../../sessionHelpers.ts";

test("global session history groups by Agent and sorts newest sessions first", () => {
  const session = (
    id: string,
    agentId: string,
    updatedAt: string,
    name = id,
  ): BackendSession => ({
    id,
    agent_id: agentId,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: updatedAt,
    is_active: true,
    name,
    metadata: {},
    agent_conversation: {
      agent_id: agentId,
      revision: 3,
      name: agentId === "agt_support" ? "支持助手" : "研究助手",
      description: "",
      starter_prompts: [],
      avatar_ref: "builtin:assistant",
      avatar_seed: agentId,
      published_at: null,
    },
  });

  const groups = groupSessionsByAgent([
    session("support-old", "agt_support", "2026-08-01T01:00:00Z"),
    session("research-new", "agt_research", "2026-08-01T04:00:00Z"),
    session("support-new", "agt_support", "2026-08-01T03:00:00Z"),
  ]);

  assert.deepEqual(groups.map(({ key, name }) => [key, name]), [
    ["agt_research", "研究助手"],
    ["agt_support", "支持助手"],
  ]);
  assert.deepEqual(
    groups.find(({ key }) => key === "agt_support")?.sessions.map(({ id }) => id),
    ["support-new", "support-old"],
  );
});

test("SessionListContent gives ordinary users a Chinese Agent Market entry and admins Agent management", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );

  assert.match(source, /key: "agentMarket"/);
  assert.match(source, /label: "专家市场"/);
  assert.match(source, /key: "agentBuilder"/);
  assert.match(source, /label: "专家管理"/);
  assert.match(source, /key: "pluginMarket"/);
  assert.match(source, /canAccessWorkbenchItem\(user, key\)/);

  const railSource = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SidebarRail.tsx"),
    "utf8",
  );
  const sidebarSource = readFileSync(
    join(process.cwd(), "src/components/panels/SessionSidebar.tsx"),
    "utf8",
  );
  assert.match(railSource, /itemKey="agentMarket"/);
  assert.match(railSource, /itemKey="agentBuilder"/);
  assert.match(railSource, /isRailItemActive\("agentBuilder"\)/);
  assert.match(railSource, /title="专家市场"/);
  assert.match(railSource, /title="专家管理"/);
  assert.match(railSource, /canAccessWorkbenchItem\(user, "pluginMarket"\)/);
  assert.match(railSource, /itemKey="pluginMarket"/);
  assert.equal(
    (sidebarSource.match(/onOpenAgentBuilder=\{\(\) => navigateWorkbenchItem\("agentBuilder"\)\}/g) ?? [])
      .length,
    2,
  );
  assert.equal(
    (sidebarSource.match(/onOpenPluginMarket=\{\(\) => navigateWorkbenchItem\("pluginMarket"\)\}/g) ?? [])
      .length,
    2,
  );
  assert.match(source, /groupSessionsByAgent/);
  assert.match(source, /data-agent-history-group/);
  assert.match(source, /aria-expanded=\{isExpanded\}/);
});

test("Agent workspace exposes global history beside task history and keeps both available on mobile", () => {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );

  assert.match(source, /groupSessionsByAgent\(globalSessions\)/);
  assert.match(source, /data-workbench-global-history/);
  assert.match(source, /data-workbench-mobile-history/);
  assert.match(source, /全部对话/);
  assert.match(source, /任务历史/);
  assert.match(source, /onSelectGlobalSession\(session\)/);
});

test("session history has no detached preview with its own event projection", () => {
  assert.equal(
    existsSync(join(process.cwd(), "src/components/sidebar/SessionPreviewDialog.tsx")),
    false,
  );
});

// Exercise the local helper without adding an exported production seam or mocking
// React hooks. The owning source remains the executable body under test.
function historyCatalogSource() {
  const source = readFileSync(
    join(process.cwd(), "src/components/panels/SidebarParts/SessionListContent.tsx"),
    "utf8",
  );
  return ts.createSourceFile("SessionListContent.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
}

function historyCatalog(collapsed: boolean, loadingCalls: () => ReactNode) {
  const source = historyCatalogSource();
  let initializer: ts.Expression | undefined;
  const visit = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && node.name.getText(source) === "renderHistoryCatalog") {
      initializer = node.initializer;
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.ok(initializer);
  const compiled = ts.transpileModule(`export const renderHistoryCatalog = ${initializer.getText(source)};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const exports: { renderHistoryCatalog?: (
    label: string, loading: boolean, hasHistory: boolean, unread: number, content: () => ReactNode,
  ) => ReactElement<{ children: ReactNode[] }> | null } = {};
  new Function("isChatsCollapsed", "renderLoadingSessions", "onToggleChatsCollapsed", "formatUnreadCount", "ChevronDown", "require", "exports", compiled)(
    collapsed, loadingCalls, () => {}, String, "chevron-test", createRequire(import.meta.url), exports,
  );
  assert.ok(exports.renderHistoryCatalog);
  return exports.renderHistoryCatalog;
}

for (const collapsed of [false, true]) {
  for (const loading of [false, true]) {
    for (const hasHistory of [false, true]) {
      test(`history factory calls: collapsed=${collapsed}, loading=${loading}, history=${hasHistory}`, () => {
        let contentCalls = 0;
        let skeletonCalls = 0;
        const content = ["first session", "second session"];
        const catalog = historyCatalog(collapsed, () => { skeletonCalls += 1; return "loading"; });
        const rendered = catalog("History", loading, hasHistory, 3, () => {
          contentCalls += 1;
          assert.equal(collapsed || loading || !hasHistory, false, "Hidden content must never be constructed");
          return content;
        });
        const shouldConstruct = !collapsed && !loading && hasHistory;
        assert.equal(contentCalls, shouldConstruct ? 1 : 0);
        assert.equal(skeletonCalls, !collapsed && loading ? 1 : 0);
        if (!hasHistory && !loading) {
          assert.equal(rendered, null);
        } else {
          assert.ok(rendered);
          assert.equal(rendered.props.children[1], collapsed ? null : loading ? "loading" : content);
        }
      });
    }
  }
}

test("all four catalog callers pass task/global factories without eager invocation", () => {
  const source = historyCatalogSource();
  const factories: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isCallExpression(node) && node.expression.getText(source) === "renderHistoryCatalog") {
      assert.equal(node.arguments.length, 5);
      factories.push(node.arguments[4].getText(source));
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  assert.deepEqual(factories, [
    "renderGlobalSessionGroups", "renderTaskHistory", "renderGlobalSessionGroups",
    "agentWorkspace ? renderTaskHistory : renderGlobalSessionGroups",
  ]);
});
