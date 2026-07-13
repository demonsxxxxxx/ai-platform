#!/usr/bin/env node
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

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

function findChrome() {
  const candidates = [
    process.env.AI_PLATFORM_CHROME_PATH,
    join(process.env.PROGRAMFILES || "", "Google/Chrome/Application/chrome.exe"),
    join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
    join(process.env.PROGRAMFILES || "", "Microsoft/Edge/Application/msedge.exe"),
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate));
}

async function getJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`http_${response.status}:${url}`);
  return response.json();
}

class CdpClient {
  constructor(webSocketUrl) {
    this.webSocketUrl = webSocketUrl;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    this.ws = new WebSocket(this.webSocketUrl);
    await new Promise((resolvePromise, rejectPromise) => {
      this.ws.addEventListener("open", resolvePromise, { once: true });
      this.ws.addEventListener("error", rejectPromise, { once: true });
    });
    this.ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      const pending = this.pending.get(payload.id);
      if (!pending) return;
      this.pending.delete(payload.id);
      if (payload.error) pending.reject(new Error(payload.error.message));
      else pending.resolve(payload.result || {});
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        result.exceptionDetails.exception?.description ||
          result.exceptionDetails.text ||
          "runtime_evaluate_failed",
      );
    }
    return result.result?.value;
  }

  async waitFor(expression, label) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const value = await this.evaluate(expression).catch(() => false);
      if (value) return value;
      await delay(150);
    }
    throw new Error(`timeout_waiting_for:${label}`);
  }

  close() {
    this.ws?.close();
  }
}

async function startBrowser(viewport) {
  const executable = findChrome();
  if (!executable) throw new Error("chrome_not_found");
  const port = 9700 + Math.floor(Math.random() * 200);
  const profile = mkdtempSync(join(tmpdir(), "company-rbac-smoke-"));
  const child = spawn(
    executable,
    [
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      "--headless=new",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--disable-dev-shm-usage",
      "--disable-gpu",
      `--window-size=${viewport.width},${viewport.height}`,
      "about:blank",
    ],
    { stdio: "ignore", windowsHide: true },
  );
  const endpoint = `http://127.0.0.1:${port}`;
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      await getJson(`${endpoint}/json/version`);
      break;
    } catch {
      await delay(150);
    }
  }
  const target = await getJson(
    `${endpoint}/json/new?${encodeURIComponent("about:blank")}`,
    { method: "PUT" },
  );
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.connect();
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: viewport.mobile,
  });
  return {
    client,
    close: async () => {
      client.close();
      child.kill();
      await delay(200);
      try {
        rmSync(profile, { recursive: true, force: true });
      } catch {
        // Chrome can retain profile handles briefly on Windows.
      }
    },
  };
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

async function screenshot(client, name) {
  mkdirSync(evidenceDir, { recursive: true });
  const result = await client.send("Page.captureScreenshot", { format: "png" });
  const path = join(evidenceDir, `${name}.png`);
  writeFileSync(path, Buffer.from(result.data, "base64"));
  return basename(path);
}

async function runCase(role, viewportName, viewport) {
  markStage(`${role}:${viewportName}:start-browser`);
  const isAdmin = role === "admin";
  const browser = await startBrowser(viewport);
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
    const screenshotName = await screenshot(client, `${role}-${viewportName}`);
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
  const failure = {
      schema_version: "ai-platform.company-rbac-browser-smoke.v1",
      synthetic_identities_only: true,
      ok: false,
      stage: currentStage,
      error: error instanceof Error ? error.message : String(error),
    };
  mkdirSync(evidenceDir, { recursive: true });
  writeFileSync(
    join(evidenceDir, "failure.json"),
    `${JSON.stringify(failure, null, 2)}\n`,
    "utf8",
  );
  console.error(JSON.stringify(failure));
  process.exitCode = 1;
});
