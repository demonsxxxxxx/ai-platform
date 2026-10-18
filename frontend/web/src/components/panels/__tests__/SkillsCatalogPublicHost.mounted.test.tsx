import assert from "node:assert/strict";
import test from "node:test";
import type { ReactNode } from "react";
import type { User } from "../../../types/auth";
import type { UserSkill } from "../../../types/skill";
import { authApi } from "../../../services/api/auth";
import { roleApi } from "../../../services/api/role";
import { installBrowserAuthTestDb } from "../../../hooks/__tests__/browserAuthTestDb";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot";

const dom = installSelectTestDom();
installBrowserAuthTestDb();
test.after(() => dom.close());
const { act, createElement } = await import("react");
const { MemoryRouter } = await import("react-router-dom");
const { AuthProvider, useAuth } = await import("../../../hooks/useAuth");
const { SkillsHubPanel } = await import("../SkillsHubPanel");
const { default: i18n } = await import("../../../i18n");

// Mirrors test_skills_and_marketplace_read_contracts_bound_source_metadata_by_role.
// expected_version is public. Empty files/zero file_count are redaction, not the
// real package's file inventory. All identifiers and responses here are synthetic.
const publicSkill: UserSkill = {
  skill_name: "qa-file-reviewer", expected_version: "hash-a",
  input_modes: ["docx"], requires_file: false,
  description: "Review Word documents.", tags: ["document"], files: [],
  enabled: true, file_count: 0, installed_from: "marketplace",
  published_marketplace_name: "qa-file-reviewer",
  created_at: "2026-06-22T00:00:00Z", updated_at: "2026-06-22T00:00:00Z",
  is_published: true, marketplace_is_active: true,
};
const otherSkill: UserSkill = {
  ...publicSkill, skill_name: "qa-spreadsheet-reviewer", expected_version: "hash-b",
  description: "Review synthetic spreadsheets.", input_modes: ["xlsx"],
  published_marketplace_name: "qa-spreadsheet-reviewer",
};
const managementPermissions = ["skill:read", "skill:write", "skill:delete", "skill:admin"];
function principal(admin: boolean, permissions = ["skill:read"]): User {
  return {
    id: admin ? "synthetic-admin" : "synthetic-reader", tenant_id: "synthetic-tenant",
    username: "synthetic", email: "synthetic@example.test", roles: [admin ? "admin" : "user"],
    permissions, is_active: true, created_at: "", updated_at: "",
  };
}
function page(skills: UserSkill[] = [publicSkill, otherSkill], permissions = ["skill:read"]) {
  return { skills, total: skills.length, skip: 0, limit: 200, available_tags: ["document"], effective_permissions: permissions };
}
function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}
type Request = { url: URL; method: string };
async function harness(user: User, list: () => Response | Promise<Response>) {
  const original = { bootstrap: authApi.bootstrapAuthContext, user: authApi.getCurrentUser, roles: roleApi.list, fetch: globalThis.fetch };
  const calls: Request[] = [];
  let auth!: ReturnType<typeof useAuth>;
  authApi.bootstrapAuthContext = async (request) => ({ status: "ready", protocol_version: 2, generation: request!.generation });
  authApi.getCurrentUser = async () => user;
  roleApi.list = async () => ({ roles: [], total: 0, skip: 0, limit: 200 });
  // Stub only HTTP transport: skillApi, its DTO parsers, useSkills composition,
  // SkillsHubPanel, SkillsPanel and the actual Lobe table remain unmocked.
  globalThis.fetch = (async (input, init = {}) => {
    const url = new URL(String(input), "http://localhost");
    calls.push({ url, method: init.method ?? "GET" });
    if (url.pathname === "/api/skills/" && !init.method) return list();
    if (url.pathname === "/api/ai/admin/skills") return json({ items: [publicSkill, otherSkill].map((skill) => ({
      skill_id: skill.skill_name, name: skill.skill_name, description: skill.description,
      lifecycle_status: "active", distribution_status: "active", visible_to_user: true,
      latest_version: skill.expected_version, latest_version_status: "released",
      current_version: skill.expected_version, latest_display_version: "Synthetic release 1",
      current_display_version: "Synthetic release 1", latest_uploaded_at: "2026-06-22T00:00:00Z", rollout_percent: 100,
    })) });
    if (url.pathname === "/api/admin/capability-distributions") return json({ capability_distributions: [] });
    if (url.pathname === "/api/admin/capability-distributions/department-directory") return json({ departments: [] });
    // Any accidental detail/file access or mutation is recorded and fails below.
    return json({ detail: "skill_not_found" }, 404);
  }) as typeof fetch;
  function Probe({ children }: { children: ReactNode }) { auth = useAuth(); return children; }
  const container = document.createElement("div");
  const root = await createLobeTestRoot(container);
  await act(async () => root.render(createElement(AuthProvider, null,
    createElement(MemoryRouter, null, createElement(Probe, { children: createElement(SkillsHubPanel) })))));
  assert.equal(auth.isAuthenticated, true);
  return { container, calls, async close() {
    await act(async () => root.unmount());
    authApi.bootstrapAuthContext = original.bootstrap; authApi.getCurrentUser = original.user;
    roleApi.list = original.roles; globalThis.fetch = original.fetch;
  } };
}
type View = Awaited<ReturnType<typeof harness>>;
function state(view: View) {
  return view.container.querySelector("[data-skills-catalog-workbench]")?.getAttribute("data-frontend-governance-state");
}
function rows(view: View) { return Array.from(view.container.querySelectorAll<HTMLElement>("[data-skill-catalog-item]")); }
function noManagement(view: View) {
  assert.equal(view.container.querySelector('[aria-haspopup="menu"], [role="checkbox"], input[type="checkbox"], input[type="file"]'), null);
  assert.equal(view.container.querySelector(".skill-detail-header__actions button"), null);
  for (const key of ["skills.importZip", "skills.exportZip", "skills.managementTable.updateVersion", "skills.managementTable.edit", "skills.managementTable.delete"]) {
    const label = i18n.t(key);
    assert.equal(Array.from(view.container.querySelectorAll("button")).some((button) => button.textContent?.trim() === label), false, `${key} must be hidden, not merely disabled`);
  }
  assert.equal(document.querySelector('[role="dialog"]'), null);
}
function onlyPublicReads(view: View) {
  assert.ok(view.calls.length > 0);
  assert.ok(view.calls.every(({ url, method }) => url.pathname === "/api/skills/" && method === "GET"), "ordinary host must not read detail/files/admin metadata or mutate");
  assert.equal(view.calls[0].url.searchParams.get("limit"), "200", "Hub loads the complete authorized catalog");
}
async function key(node: HTMLElement, value: string) {
  await act(async () => {
    node.focus();
    node.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true, cancelable: true }));
    node.dispatchEvent(new KeyboardEvent("keyup", { key: value, bubbles: true, cancelable: true }));
  });
}

for (const permissions of [["skill:read"], managementPermissions]) {
  test(`ordinary host retains public selection and version with permissions ${permissions.join(",")}`, async () => {
    const view = await harness(principal(false, permissions), () => json(page(undefined, permissions)));
    try {
      assert.equal(state(view), "ready"); assert.equal(rows(view).length, 2);
      assert.match(view.container.textContent!, /Review Word documents\./);
      assert.match(view.container.querySelector("[data-selected-skill-detail]")!.textContent!, /hash-a/);
      noManagement(view);
      await key(rows(view)[1], "Enter");
      assert.equal(rows(view)[1].getAttribute("aria-selected"), "true");
      assert.match(view.container.querySelector("[data-selected-skill-detail]")!.textContent!, /hash-b.*xlsx/s);
      await key(rows(view)[0], " ");
      assert.equal(rows(view)[0].getAttribute("aria-selected"), "true");
      assert.doesNotMatch(view.container.innerHTML, /SKILL\.md|references\/guide\.md|private_manifest|storage_key|runtime_path|credential/);
      noManagement(view); onlyPublicReads(view);
    } finally { await view.close(); }
  });
}

test("unexpected private transport fields do not enter the ordinary host DOM", async () => {
  // Defense-in-depth fault injection is separate from the valid backend fixture.
  // It still crosses real HTTP parsing and composition, never a mocked hook.
  const hostile = { ...publicSkill, private_manifest: "SYNTHETIC_PRIVATE_MANIFEST", storage_key: "SYNTHETIC_STORAGE_KEY", runtime_path: "SYNTHETIC_RUNTIME_PATH", credential: "SYNTHETIC_CREDENTIAL", source: { package: "SYNTHETIC_SOURCE_TREE" }, content: "SYNTHETIC_FILE_CONTENT" };
  const view = await harness(principal(false), () => json(page([hostile])));
  try {
    assert.equal(state(view), "ready"); assert.equal(rows(view).length, 1);
    assert.match(view.container.textContent!, /hash-a/);
    assert.doesNotMatch(view.container.innerHTML + document.body.innerHTML, /SYNTHETIC_(PRIVATE_MANIFEST|STORAGE_KEY|RUNTIME_PATH|CREDENTIAL|SOURCE_TREE|FILE_CONTENT)/);
    noManagement(view); onlyPublicReads(view);
  } finally { await view.close(); }
});

for (const admin of [false, true]) {
  test(`${admin ? "admin" : "ordinary"} host with pending catalog hides actions until resolution`, async () => {
    const pending = deferred<Response>();
    const view = await harness(principal(admin, managementPermissions), () => pending.promise);
    try {
      assert.equal(state(view), "loading"); assert.equal(rows(view).length, 0);
      noManagement(view);
      await act(async () => pending.resolve(json(page(undefined, managementPermissions))));
      assert.equal(state(view), "ready"); assert.equal(rows(view).length, 2);
      if (!admin) { noManagement(view); onlyPublicReads(view); }
      assert.ok(view.calls.every(({ method }) => method === "GET"));
    } finally { await view.close(); }
  });

  test(`${admin ? "admin" : "ordinary"} host permission denial is not an empty successful catalog`, async () => {
    const view = await harness(principal(admin, managementPermissions), () => json({ detail: "missing_permission:skill:read" }, 403));
    try {
      await act(async () => {});
      assert.equal(state(view), "forbidden", `403 host state; remaining buttons: ${Array.from(view.container.querySelectorAll("button")).map((button) => button.textContent?.trim()).join(" | ")}`);
      if (admin) {
        // Public read denial does not revoke the separately authorized lifecycle
        // catalog. It must not restore public runtime detail or runtime actions.
        assert.equal(rows(view).length, 2);
        assert.ok(view.calls.some(({ url }) => url.pathname === "/api/ai/admin/skills"));
        assert.match(view.container.querySelector("[data-skill-authority-rail]")!.textContent!, /Synthetic release 1/);
        assert.equal(view.container.querySelector("[data-selected-skill-detail]"), null);
        for (const row of rows(view)) {
          assert.equal(row.querySelector('[aria-haspopup="menu"]'), null);
        }
        for (const key of ["skills.exportZip", "skills.managementTable.edit", "skills.managementTable.delete"]) {
          assert.equal(Array.from(view.container.querySelectorAll("button")).some((button) => button.textContent?.trim() === i18n.t(key)), false);
        }
        assert.equal(view.calls.some(({ url }) => /^\/api\/skills\/[^/]/.test(url.pathname)), false);
      } else {
        assert.equal(rows(view).length, 0);
        noManagement(view);
      }
      assert.doesNotMatch(view.container.textContent!, /missing_permission:skill:read/);
      assert.equal(view.calls.filter(({ url }) => url.pathname === "/api/skills/").length, 1, "denial must not self-retry");
      assert.ok(view.calls.every(({ method }) => method === "GET"));
      if (!admin) onlyPublicReads(view);
    } finally { await view.close(); }
  });
}

test("admin host retains lifecycle metadata and archive menu keyboard isolation", async () => {
  const adminSkills = [publicSkill, otherSkill].map((skill) => ({ ...skill, files: ["SKILL.md", "references/guide.md"], file_count: 2 }));
  const view = await harness(principal(true, managementPermissions), () => json(page(adminSkills, managementPermissions)));
  try {
    assert.equal(state(view), "ready"); assert.equal(rows(view).length, 2);
    assert.ok(view.calls.some(({ url }) => url.pathname === "/api/ai/admin/skills"));
    assert.ok(view.calls.some(({ url }) => url.pathname === "/api/admin/capability-distributions"));
    assert.ok(view.container.querySelector(".skill-detail-header__actions button"));
    assert.match(view.container.querySelector("[data-skill-authority-rail]")!.textContent!, /Synthetic release 1/);
    const first = rows(view)[0]; const second = rows(view)[1];
    assert.equal(first.getAttribute("aria-selected"), "true");
    const menu = second.querySelector<HTMLButtonElement>('[aria-haspopup="menu"]');
    assert.ok(menu);
    await act(async () => menu.click());
    const items = Array.from(document.querySelectorAll<HTMLElement>('[role="menuitem"]'));
    for (const label of ["skills.managementTable.edit", "skills.exportZip", "skills.managementTable.delete"]) {
      assert.ok(items.some((item) => item.textContent?.trim() === i18n.t(label)), `${label} remains available to admin`);
    }
    const archive = items.find((item) => item.textContent?.trim() === i18n.t("skills.managementTable.delete"))!;
    assert.equal(view.container.querySelector("table")!.contains(archive), false, "real Lobe menu uses its portal");
    await key(archive, "Enter");
    assert.equal(first.getAttribute("aria-selected"), "true", "menu keyboard activation must not change the selected detail");
    assert.equal(second.getAttribute("aria-selected"), "false");
    assert.ok(document.querySelector('[role="dialog"], [role="alertdialog"]'), "archive opens confirmation before mutation");
    assert.ok(view.calls.every(({ method }) => method === "GET"), "opening archive confirmation must not delete");
    assert.equal(view.calls.some(({ url }) => /^\/api\/skills\/[^/]/.test(url.pathname)), false, "row selection/menu opening must not load package files");
  } finally { await view.close(); }
});
