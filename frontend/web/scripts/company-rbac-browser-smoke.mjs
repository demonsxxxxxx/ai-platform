#!/usr/bin/env node
import {
  appendFileSync,
  mkdirSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import {
  captureScreenshot,
  redactedValue,
  sleep as delay,
  startBrowser,
} from "./browser-smoke-harness.mjs";

const baseUrl = (
  process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:4173"
).replace(/\/+$/, "");
const evidenceDir = resolve(
  process.env.AI_PLATFORM_RBAC_SMOKE_DIR ||
    "../../.codex-tmp/company-rbac-browser-smoke",
);
const timeoutMs = 30_000;
let currentStage = "initializing";
const adminItems = ["channels", "agents", "models"];
const adminMenuPaths = [
  "/channels",
  "/agents",
  "/models",
  "/users",
  "/settings",
  "/feedback",
];

function markStage(stage) {
  currentStage = stage;
  mkdirSync(evidenceDir, { recursive: true });
  writeFileSync(join(evidenceDir, "stage.txt"), `${stage}\n`, "utf8");
  appendFileSync(
    join(evidenceDir, "stage-history.log"),
    `${new Date().toISOString()} ${stage}\n`,
    "utf8",
  );
}

function recordBrowserEvent(event, details) {
  mkdirSync(evidenceDir, { recursive: true });
  const entry = redactedValue({ at: new Date().toISOString(), event, details });
  appendFileSync(
    join(evidenceDir, "browser-events.jsonl"),
    `${JSON.stringify(entry)}\n`,
    "utf8",
  );
}

function mockBootstrapSource(isAdmin) {
  const role = isAdmin ? "admin" : "user";
  const permissions = isAdmin
    ? ["chat:read", "chat:write", "session:read", "session:write", "user:read"]
    : ["chat:read", "chat:write", "session:read", "session:write"];
  return `(() => {
    localStorage.removeItem("language");
    localStorage.removeItem("i18nextLng");
    const originalFetch = window.fetch.bind(window);
    const state = window.__companyRbacSmoke = {
      errors: [],
      adminContentSeen: false,
    };
    window.addEventListener("error", (event) => state.errors.push(String(event.message)));
    window.addEventListener("unhandledrejection", (event) => state.errors.push(String(event.reason)));
    const observe = () => {
      const inspect = () => {
        if (document.querySelector('[data-workbench-projection-page="users"]')) {
          state.adminContentSeen = true;
        }
      };
      inspect();
      new MutationObserver(inspect).observe(document.documentElement, { childList: true, subtree: true });
    };
    if (document.documentElement) observe();
    else document.addEventListener("DOMContentLoaded", observe, { once: true });
    const json = (value, status = 200) => new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    });
    const governance = {
      projection: "synthetic",
      tenant_id: "tenant-synthetic",
      workspace_id: "workspace-synthetic",
      degraded: false,
      audit_required: true,
      rollback_available: false,
      secret_material_projected: false,
    };
    window.fetch = async (input, init = {}) => {
      const url = new URL(typeof input === "string" ? input : input.url, location.origin);
      if (url.pathname === "/api/ai/auth/me") return json({
        user_id: "synthetic-${role}",
        user_name: "synthetic-${role}",
        display_name: "Synthetic ${isAdmin ? "Admin" : "User"}",
        tenant_id: "tenant-synthetic",
        roles: [${JSON.stringify(role)}],
        permissions: ${JSON.stringify(permissions)},
        is_admin: ${isAdmin},
        source: "company-login",
      });
      if (url.pathname === "/api/users/") {
        const projectedUser = {
          id: "synthetic-user-record",
          username: "synthetic-user-record",
          email: null,
          full_name: "Synthetic User",
          is_active: true,
          is_superuser: false,
          roles: ["user"],
          permissions: [],
          tenant_id: "synthetic-tenant",
          department_id: "",
          created_at: null,
          updated_at: null,
        };
        return json({
          users: [projectedUser], items: [projectedUser], total: 1, skip: 0, limit: 20, governance,
        });
      }
      if (url.pathname === "/api/agent/models/available") return json({
        models: [{ id: "synthetic-model", value: "mock/model", label: "Synthetic Model", provider: "mock" }],
        count: 1, enabled_count: 1, default_model_id: "synthetic-model",
      });
      if (url.pathname === "/api/auth/profile") return json({ metadata: {} });
      if (url.pathname === "/agents") return json({
        agents: [{ id: "general-agent", name: "General Agent", description: "Synthetic", options: {} }],
        default_agent: "general-agent", allowed_model_ids: ["synthetic-model"],
      });
      if (url.pathname.includes("persona")) return json({ presets: [], total: 0, skip: 0, limit: 12, available_tags: [] });
      if (url.pathname.includes("session")) return json({ sessions: [], total: 0, runs: [], events: [] });
      if (url.pathname.includes("notification")) return json([]);
      if (url.pathname.includes("mcp")) return json({ servers: [] });
      if (url.pathname.startsWith("/api/")) return json({ items: [], total: 0, governance });
      return originalFetch(input, init);
    };
  })();`;
}

async function navigate(client, path) {
  await client.send("Page.navigate", { url: `${baseUrl}${path}` });
  await client.waitFor(
    "document.readyState === 'complete' || document.readyState === 'interactive'",
    `document:${path}`,
  );
}

async function runCase(role, viewportName, viewport) {
  markStage(`${role}:${viewportName}:start-browser`);
  const isAdmin = role === "admin";
  const browser = await startBrowser({
    viewport,
    timeoutMs,
    profilePrefix: "company-rbac-smoke-",
    report: recordBrowserEvent,
  });
  const { client } = browser;
  try {
    await client.send("Page.addScriptToEvaluateOnNewDocument", {
      source: mockBootstrapSource(isAdmin),
    });
    markStage(`${role}:${viewportName}:navigate-chat`);
    await navigate(client, "/chat");
    await client.waitFor(
      "Boolean(document.querySelector('[data-librechat-shell], [data-workbench-sidebar-panel], [data-librechat-rail]'))",
      `${role}:${viewportName}:shell`,
    );
    await client.evaluate(`(() => {
      const expand = document.querySelector('[data-librechat-rail] button[aria-expanded="false"]');
      if (expand) expand.click();
      return true;
    })()`);
    await delay(250);
    await client.evaluate(`(() => {
      const trigger = document.querySelector('[data-user-menu-trigger]');
      if (trigger) trigger.click();
      return true;
    })()`);
    await delay(250);
    const navigation = await client.evaluate(`(() => {
      const visible = (node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
      };
      const adminItems = ${JSON.stringify(adminItems)};
      const adminMenuPaths = ${JSON.stringify(adminMenuPaths)};
      const railCount = adminItems.filter((item) =>
        Array.from(document.querySelectorAll('[data-workbench-rail-item="' + item + '"]')).some(visible)
      ).length;
      const panelCount = adminItems.filter((item) =>
        Array.from(document.querySelectorAll('[data-workbench-nav-item="' + item + '"]')).some(visible)
      ).length;
      const menuCount = adminMenuPaths.filter((path) =>
        Array.from(document.querySelectorAll('[data-workbench-user-menu-item="' + path + '"]')).some(visible)
      ).length;
      return { railCount, panelCount, menuCount };
    })()`);
    const language = await client.evaluate(`({
      hasChinese: /[\\u3400-\\u9fff]/.test(document.body.innerText),
      savedLanguage: localStorage.getItem("language"),
    })`);
    await navigate(client, "/users");
    markStage(`${role}:${viewportName}:navigate-users`);
    if (isAdmin) {
      await client.waitFor("location.pathname === '/users'", `${role}:${viewportName}:admin-route`);
      await client.waitFor(
        "Boolean(document.querySelector('[data-workbench-projection-page=\"users\"], [data-frontend-governance-state]'))",
        `${role}:${viewportName}:admin-content`,
      );
    } else {
      await client.waitFor("location.pathname === '/chat'", `${role}:${viewportName}:redirect`);
      await client.waitFor(
        "Boolean(document.querySelector('[data-librechat-shell], [data-workbench-sidebar-panel], [data-librechat-rail]'))",
        `${role}:${viewportName}:redirected-shell`,
      );
    }
    const layout = await client.evaluate(`(() => {
      const selector = '[data-workbench-rail-item], [data-workbench-nav-item], [data-workbench-user-menu-item]';
      const nodes = Array.from(document.querySelectorAll(selector)).filter((node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
      });
      const overlaps = [];
      for (let i = 0; i < nodes.length; i += 1) {
        const a = nodes[i].getBoundingClientRect();
        for (let j = i + 1; j < nodes.length; j += 1) {
          if (nodes[i].contains(nodes[j]) || nodes[j].contains(nodes[i])) continue;
          const b = nodes[j].getBoundingClientRect();
          const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          if (width > 2 && height > 2) overlaps.push([i, j]);
        }
      }
      return {
        width: innerWidth,
        height: innerHeight,
        bodyScrollWidth: document.documentElement.scrollWidth,
        overlaps,
        finalPath: location.pathname,
        adminContentSeen: window.__companyRbacSmoke.adminContentSeen,
        errors: window.__companyRbacSmoke.errors,
      };
    })()`);
    const screenshotName = await captureScreenshot(
      client,
      evidenceDir,
      `${role}-${viewportName}`,
    );
    const managementNavigationOk = isAdmin
      ? Math.max(navigation.railCount, navigation.panelCount) === 3
      : navigation.railCount === 0 && navigation.panelCount === 0;
    const ok =
      managementNavigationOk &&
      navigation.menuCount === (isAdmin ? adminMenuPaths.length : 0) &&
      language.hasChinese === true &&
      language.savedLanguage === null &&
      layout.bodyScrollWidth <= layout.width &&
      layout.overlaps.length === 0 &&
      layout.errors.length === 0 &&
      (isAdmin
        ? layout.finalPath === "/users" && layout.adminContentSeen === true
        : layout.finalPath === "/chat" && layout.adminContentSeen === false);
    return {
      role,
      viewportName,
      viewport,
      navigation,
      language,
      layout,
      screenshot: screenshotName,
      ok,
    };
  } catch (error) {
    markStage(`${role}:${viewportName}:failed`);
    await captureScreenshot(client, evidenceDir, `failure-${role}-${viewportName}`).catch(
      () => null,
    );
    throw error;
  } finally {
    await browser.close();
  }
}

async function main() {
  markStage("main:start");
  const cases = [];
  for (const [viewportName, viewport] of Object.entries({
    desktop: { width: 1440, height: 900, mobile: false },
    mobile: { width: 390, height: 844, mobile: true },
  })) {
    for (const role of ["user", "admin"]) {
      cases.push(await runCase(role, viewportName, viewport));
    }
  }
  const evidence = {
    schema_version: "ai-platform.company-rbac-browser-smoke.v1",
    generated_at: new Date().toISOString(),
    synthetic_identities_only: true,
    cases,
    ok: cases.every((item) => item.ok),
  };
  mkdirSync(evidenceDir, { recursive: true });
  writeFileSync(
    join(evidenceDir, "evidence.json"),
    `${JSON.stringify(evidence, null, 2)}\n`,
    "utf8",
  );
  console.log(JSON.stringify(evidence, null, 2));
  if (!evidence.ok) process.exitCode = 1;
}

main().catch((error) => {
  const errorMessage = error instanceof Error ? error.message : String(error);
  const failure = redactedValue({
      schema_version: "ai-platform.company-rbac-browser-smoke.v1",
      synthetic_identities_only: true,
      ok: false,
      stage: currentStage,
      error: errorMessage,
    });
  mkdirSync(evidenceDir, { recursive: true });
  writeFileSync(
    join(evidenceDir, "failure.json"),
    `${JSON.stringify(failure, null, 2)}\n`,
    "utf8",
  );
  console.error(JSON.stringify(failure));
  process.exitCode = 1;
});
