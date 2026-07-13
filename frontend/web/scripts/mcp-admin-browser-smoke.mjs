#!/usr/bin/env node
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

const root = resolve(import.meta.dirname, "..");
const outputDir = resolve(root, "test-results", "mcp-admin-browser-smoke");
const chromeCandidates = [
  process.env.AI_PLATFORM_CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  join(process.env.LOCALAPPDATA || "", "Google\\Chrome\\Application\\chrome.exe"),
  join(process.env.PROGRAMFILES || "", "Microsoft\\Edge\\Application\\msedge.exe"),
].filter(Boolean);
const chrome = chromeCandidates.find((candidate) => {
  try {
    return existsSync(candidate);
  } catch {
    return false;
  }
});
const timeoutMs = 30_000;

function diagnostic(stage, extra = {}) {
  mkdirSync(outputDir, { recursive: true });
  appendFileSync(
    join(outputDir, "diagnostic.ndjson"),
    `${JSON.stringify({ stage, ...extra, at: new Date().toISOString() })}\n`,
  );
}

function safeError(error) {
  if (error instanceof Error) {
    return error.stack || error.message;
  }
  return String(error);
}

function trimText(value, max = 2400) {
  if (typeof value !== "string") {
    return value;
  }
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function appendRing(ring, chunk) {
  const lines = String(chunk)
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean);
  if (!lines.length) {
    return;
  }
  ring.push(...lines);
  if (ring.length > 80) {
    ring.splice(0, ring.length - 80);
  }
}

function trackChild(child, name) {
  const stdout = [];
  const stderr = [];
  child.stdout?.on("data", (chunk) => appendRing(stdout, chunk));
  child.stderr?.on("data", (chunk) => appendRing(stderr, chunk));
  child.once("error", (error) => {
    diagnostic(`${name}_child_error`, { error: safeError(error) });
  });
  child.once("exit", (code, signal) => {
    diagnostic(`${name}_child_exit`, { code, signal });
  });
  return { stdout, stderr };
}

async function withTimeout(promise, label, extra = {}) {
  return Promise.race([
    promise,
    delay(timeoutMs).then(() => {
      throw new Error(`timeout:${label}:${JSON.stringify(extra)}`);
    }),
  ]);
}

async function findFreePort() {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer();
    server.unref();
    server.once("error", rejectPromise);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port =
        typeof address === "object" && address ? address.port : null;
      server.close((closeError) => {
        if (closeError) {
          rejectPromise(closeError);
          return;
        }
        if (!port) {
          rejectPromise(new Error("free_port_unavailable"));
          return;
        }
        resolvePromise(port);
      });
    });
  });
}

async function httpJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    throw new Error(`http_${response.status}:${url}`);
  }
  return response.json();
}

async function waitForHttpReady(url, childLogs, name) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
      if (response.ok) {
        return;
      }
    } catch {
      // Keep polling until timeout.
    }
    await delay(150);
  }
  throw new Error(
    [
      `${name}_not_ready:${url}`,
      `stdout=${JSON.stringify(childLogs.stdout)}`,
      `stderr=${JSON.stringify(childLogs.stderr)}`,
    ].join("\n"),
  );
}

class CdpClient {
  constructor(webSocketUrl) {
    this.webSocketUrl = webSocketUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Map();
  }

  on(method, handler) {
    const handlers = this.handlers.get(method) || [];
    handlers.push(handler);
    this.handlers.set(method, handlers);
  }

  emit(method, params) {
    const handlers = this.handlers.get(method) || [];
    handlers.forEach((handler) => {
      try {
        handler(params);
      } catch (error) {
        diagnostic("cdp_handler_error", {
          method,
          error: safeError(error),
        });
      }
    });
  }

  async connect() {
    this.ws = new WebSocket(this.webSocketUrl);
    await withTimeout(
      new Promise((resolvePromise, rejectPromise) => {
        this.ws.addEventListener("open", resolvePromise, { once: true });
        this.ws.addEventListener("error", rejectPromise, { once: true });
      }),
      "cdp_connect",
      { webSocketUrl: this.webSocketUrl },
    );

    this.ws.addEventListener("message", (event) => {
      const payload = JSON.parse(event.data);
      if (typeof payload.id === "number") {
        const pending = this.pending.get(payload.id);
        if (!pending) return;
        this.pending.delete(payload.id);
        if (payload.error) {
          pending.reject(new Error(payload.error.message));
        } else {
          pending.resolve(payload.result || {});
        }
        return;
      }
      if (payload.method) {
        this.emit(payload.method, payload.params || {});
      }
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    return withTimeout(
      new Promise((resolvePromise, rejectPromise) => {
        this.pending.set(id, {
          resolve: resolvePromise,
          reject: rejectPromise,
        });
        this.ws.send(JSON.stringify({ id, method, params }));
      }),
      `cdp_send:${method}`,
      { method },
    );
  }

  async evaluate(expression, label = "evaluate") {
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
          `runtime_evaluate_failed:${label}`,
      );
    }
    return result.result?.value;
  }

  async waitFor(expression, label) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const value = await this.evaluate(expression, label).catch(() => false);
      if (value) {
        return value;
      }
      await delay(150);
    }
    throw new Error(`timeout_waiting_for:${label}`);
  }

  close() {
    this.ws?.close();
  }
}

function versionInfo() {
  return {
    app_version: "smoke-local",
    git_tag: "smoke-local",
    commit_hash: "smoke-local",
    build_time: new Date().toISOString(),
    github_url: "https://github.com/demonsxxxxxx/ai-platform",
    has_update: false,
    last_checked: new Date().toISOString(),
  };
}

function sessionList() {
  return {
    sessions: [],
    total: 0,
    skip: 0,
    limit: 20,
    has_more: false,
  };
}

function bootstrapSource(admin, mode = "listing") {
  const permissions = admin
    ? ["mcp:read", "mcp:admin", "mcp:write_http", "mcp:delete"]
    : ["mcp:read"];
  const roles = admin ? ["admin"] : ["user"];
  return `(() => {
    const nativeFetch = window.fetch.bind(window);
    localStorage.setItem("language", "zh");
    localStorage.setItem("ai_platform_session_present", "synthetic-session");
    const applyDocumentLanguage = () => {
      if (document.documentElement) {
        document.documentElement.lang = "zh-CN";
      }
    };
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", applyDocumentLanguage, { once: true });
    } else {
      applyDocumentLanguage();
    }
    const state = (window.__mcpAdminSmoke = {
      fetches: [],
      console: [],
      errors: [],
      created: ${mode !== "empty"},
      createdBody: null,
    });
    const toText = (value) => {
      if (typeof value === "string") return value;
      if (value && typeof value === "object" && "stack" in value) return String(value.stack);
      try { return JSON.stringify(value); } catch { return String(value); }
    };
    const wrapConsole = (level) => {
      const original = console[level]?.bind(console);
      if (!original) return;
      console[level] = (...args) => {
        state.console.push({ level, args: args.map(toText) });
        return original(...args);
      };
    };
    wrapConsole("error");
    wrapConsole("warn");
    window.addEventListener("error", (event) => {
      state.errors.push({
        type: "error",
        message: toText(event.error || event.message),
        source: event.filename || event.target?.src || event.target?.href || null,
      });
    }, true);
    window.addEventListener("unhandledrejection", (event) => {
      state.errors.push({
        type: "unhandledrejection",
        message: toText(event.reason),
      });
    });
    const json = (value, status = 200) =>
      new Response(JSON.stringify(value), {
        status,
        headers: { "Content-Type": "application/json" },
      });
    const server = {
      name: ${JSON.stringify(mode === "sandbox-edit" ? "synthetic-sandbox" : "synthetic-mcp")},
      transport: ${JSON.stringify(mode === "sandbox-edit" ? "sandbox" : "streamable_http")},
      enabled: true,
      is_system: true,
      can_edit: ${admin},
      owner_user_id: null,
      allowed_roles: ["developer"],
      allowed_departments: ["engineering"],
      role_quotas: {},
      credential_state: "configured",
      credential_metadata: { endpoint_configured: true },
      url: "https://mcp-sensitive-endpoint-canary.invalid",
      headers: { Authorization: "MCP_SENSITIVE_HEADER_CANARY" },
      command: "MCP_SENSITIVE_COMMAND_CANARY",
      env_keys: ["MCP_SENSITIVE_ENV_CANARY"],
      description: "",
      updated_at: "2026-07-13T00:00:00Z",
      created_at: "2026-07-13T00:00:00Z",
    };
    window.fetch = async (input, init = {}) => {
      const rawUrl = typeof input === "string" ? input : input.url;
      const url = new URL(rawUrl, location.origin);
      const method = String(init.method || (typeof input === "object" && input.method) || "GET").toUpperCase();
      state.fetches.push({ path: url.pathname + url.search, method });
      if (url.pathname === "/api/ai/auth/me") {
        return json({
          user_id: "synthetic-admin",
          user_name: "synthetic-admin",
          display_name: "Synthetic Admin",
          tenant_id: "tenant-smoke",
          roles: ${JSON.stringify(roles)},
          permissions: ${JSON.stringify(permissions)},
          is_admin: ${admin},
          source: "cookie_session",
        });
      }
      if (url.pathname === "/api/auth/permissions") {
        return json({
          groups: [],
          all_permissions: ${JSON.stringify(permissions.map((permission) => ({
            value: permission,
            label: permission,
            description: permission,
          })))},
        });
      }
      if (url.pathname === "/api/roles" || url.pathname === "/api/roles/") {
        return json({
          roles: [{ name: "developer", description: "Developer", is_system: true }],
          total: 1,
          skip: 0,
          limit: 200,
        });
      }
      if (url.pathname === "/api/version") return json(${JSON.stringify(versionInfo())});
      if (url.pathname === "/api/agent/models/available") {
        return json({
          models: [{ id: "model-1", value: "mock/model", label: "Mock Model", provider: "mock" }],
          count: 1,
          enabled_count: 1,
          default_model_id: "model-1",
        });
      }
      if (url.pathname === "/api/auth/profile") {
        return json({ metadata: { pinned_model_ids: [] } });
      }
      if (url.pathname === "/api/auth/profile/metadata" && method === "PUT") {
        let body = {};
        try { body = JSON.parse(init.body || "{}"); } catch {}
        return json({ metadata: body.metadata || {} });
      }
      if (url.pathname === "/api/sessions" || url.pathname === "/api/sessions/") return json(${JSON.stringify(sessionList())});
      if (url.pathname.startsWith("/api/sessions/")) return json({ id: "session-smoke", agent_id: "general-agent", created_at: "2026-07-13T00:00:00Z", updated_at: "2026-07-13T00:00:00Z", is_active: true, metadata: {} });
      if (url.pathname === "/api/mcp" || url.pathname === "/api/mcp/") {
        const servers = state.created ? [server] : [];
        return json({ servers, total: servers.length, skip: 0, limit: 20 });
      }
      if (url.pathname.startsWith("/api/mcp/")) {
        if (url.pathname.endsWith("/toggle") && method === "PATCH") {
          return json({ server: { ...server, enabled: !server.enabled }, message: "ok" });
        }
        return json(server);
      }
      if (url.pathname === "/api/admin/mcp/" && method === "POST") {
        try { state.createdBody = JSON.parse(init.body || "{}"); } catch { state.createdBody = {}; }
        state.created = true;
        return json(server);
      }
      if (url.pathname.startsWith("/api/admin/mcp/")) {
        if (method === "DELETE") return json({});
        return json(server);
      }
      if (url.pathname.startsWith("/api/")) {
        return json({ items: [], total: 0, notifications: [], projects: [], runs: [], events: [] });
      }
      return nativeFetch(input, init);
    };
  })();`;
}

async function startBrowser(viewport, scenario) {
  if (!chrome) {
    throw new Error("chrome_not_found");
  }
  const port = await findFreePort();
  const endpoint = `http://127.0.0.1:${port}`;
  const profile = mkdtempSync(join(tmpdir(), "mcp-admin-smoke-"));
  const child = spawn(
    chrome,
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
    {
      stdio: "pipe",
      windowsHide: true,
    },
  );
  const logs = trackChild(child, `chrome_${scenario}`);
  const requestUrls = new Map();
  await waitForHttpReady(`${endpoint}/json/version`, logs, "chrome");
  diagnostic("chrome_ready", { case: scenario, port });
  const target = await httpJson(
    `${endpoint}/json/new?${encodeURIComponent("about:blank")}`,
    { method: "PUT" },
  );
  diagnostic("target_created", { case: scenario });
  const client = new CdpClient(target.webSocketDebuggerUrl);
  const runtime = {
    console: [],
    pageErrors: [],
    failedRequests: [],
  };
  await client.connect();
  diagnostic("cdp_connected", { case: scenario });
  client.on("Runtime.consoleAPICalled", (payload) => {
    runtime.console.push({
      type: payload.type,
      args: (payload.args || []).map((item) => item.value ?? item.description),
    });
  });
  client.on("Runtime.exceptionThrown", (payload) => {
    runtime.pageErrors.push(
      payload.exceptionDetails?.exception?.description ||
        payload.exceptionDetails?.text ||
        "runtime_exception",
    );
  });
  client.on("Log.entryAdded", (payload) => {
    runtime.pageErrors.push(
      `${payload.entry.level}:${payload.entry.text || payload.entry.url || "log_entry"}`,
    );
  });
  client.on("Network.requestWillBeSent", (payload) => {
    requestUrls.set(payload.requestId, payload.request.url);
  });
  client.on("Network.loadingFailed", (payload) => {
    runtime.failedRequests.push({
      url: requestUrls.get(payload.requestId) || null,
      errorText: payload.errorText,
      canceled: payload.canceled,
    });
  });
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Network.enable");
  await client.send("Log.enable");
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: viewport.width,
    height: viewport.height,
    deviceScaleFactor: 1,
    mobile: viewport.mobile,
  });
  return {
    client,
    runtime,
    close: async () => {
      client.close();
      child.kill();
      await delay(300);
      try {
        rmSync(profile, { recursive: true, force: true });
      } catch {
        // Chrome on Windows may still hold profile files briefly.
      }
    },
  };
}

async function startVite() {
  const port = await findFreePort();
  const child = spawn(
    process.execPath,
    [
      resolve(root, "node_modules/vite/bin/vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    {
      cwd: root,
      stdio: "pipe",
      windowsHide: true,
    },
  );
  const logs = trackChild(child, "vite");
  await waitForHttpReady(`http://127.0.0.1:${port}/`, logs, "vite");
  diagnostic("vite_ready", { port });
  return {
    port,
    logs,
    close: async () => {
      child.kill();
      await delay(200);
    },
  };
}

async function collectPageEvidence(client, scenario, runtime) {
  const page = await client
    .evaluate(
      `(() => ({
        href: window.location.href,
        title: document.title,
        readyState: document.readyState,
        bodyText: document.body?.innerText || "",
        html: document.documentElement?.outerHTML || "",
        shellVisible: Boolean(document.querySelector("[data-mcp-directory-shell]")),
        smokeState: window.__mcpAdminSmoke || null,
      }))()`,
      `${scenario}:page_evidence`,
    )
    .catch((error) => ({
      href: "evaluation_failed",
      bodyText: safeError(error),
      html: "",
      shellVisible: false,
      smokeState: null,
    }));
  return {
    finalUrl: page.href,
    title: page.title,
    readyState: page.readyState,
    shellVisible: page.shellVisible,
    bodyText: trimText(page.bodyText),
    htmlSnippet: trimText(page.html, 3200),
    console: runtime.console.slice(-20),
    pageErrors: runtime.pageErrors.slice(-20),
    failedRequests: runtime.failedRequests.slice(-20),
    mockedFetches: page.smokeState?.fetches?.slice(-40) || [],
    mockedConsole: page.smokeState?.console?.slice(-20) || [],
    mockedErrors: page.smokeState?.errors?.slice(-20) || [],
  };
}

async function writeScreenshot(client, name) {
  const result = await client.send("Page.captureScreenshot", { format: "png" });
  const path = join(outputDir, `${name}.png`);
  writeFileSync(path, Buffer.from(result.data, "base64"));
  return path;
}

async function clickText(client, labels, label) {
  const clicked = await client.evaluate(`(() => {
    const labels = ${JSON.stringify(labels)};
    const buttons = Array.from(document.querySelectorAll("button"));
    const target = buttons.find((button) =>
      labels.some((text) => button.textContent?.includes(text)),
    );
    if (!target) {
      return false;
    }
    target.click();
    return true;
  })()`, label);
  if (!clicked) {
    throw new Error(`button_not_found:${labels.join("|")}`);
  }
}

async function clickAriaLabel(client, ariaLabel, label) {
  const clicked = await client.evaluate(`(() => {
    const ariaLabel = ${JSON.stringify(ariaLabel)};
    const target = Array.from(document.querySelectorAll("button")).find(
      (button) => button.getAttribute("aria-label") === ariaLabel,
    );
    if (!target) {
      return false;
    }
    target.click();
    return true;
  })()`, label);
  if (!clicked) {
    throw new Error(`button_aria_label_not_found:${ariaLabel}`);
  }
}

async function runCase(port, role, viewport, mode = "listing") {
  const scenario =
    mode === "listing"
      ? `${role}-${viewport.width}x${viewport.height}`
      : `${role}-${mode}-${viewport.width}x${viewport.height}`;
  const browser = await startBrowser(viewport, scenario);
  const { client, runtime } = browser;
  try {
    await client.send("Page.addScriptToEvaluateOnNewDocument", {
      source: bootstrapSource(role === "admin", mode),
    });
    await client.send("Page.navigate", { url: `http://127.0.0.1:${port}/mcp` });
    diagnostic("page_navigated", { case: scenario, url: `http://127.0.0.1:${port}/mcp` });
    await client.waitFor(
      'document.documentElement.lang === "zh-CN" || localStorage.getItem("language") === "zh"',
      `${scenario}:language_ready`,
    );
    diagnostic("language_ready", { case: scenario });
    await client.waitFor(
      'Boolean(document.querySelector(\'[data-mcp-directory-shell][data-frontend-governance-state="ready"]\'))',
      `${scenario}:ready_directory_visible`,
    );
    diagnostic("ready_directory_visible", { case: scenario });

    if (mode === "empty") {
      await client.waitFor(
        'document.body.innerText.includes("添加第一个 MCP 服务器")',
        `${scenario}:empty_cta_visible`,
      );
      const emptyScreenshotPath = await writeScreenshot(
        client,
        `${scenario}-empty`,
      );
      diagnostic("screenshot_written", {
        case: scenario,
        screenshotPath: emptyScreenshotPath,
      });
      await clickText(
        client,
        ["添加第一个 MCP 服务器"],
        `${scenario}:open_empty_cta`,
      );
      await client.waitFor(
        'Boolean(document.querySelector(\'input[placeholder="my-mcp-server"]\'))',
        `${scenario}:create_form_visible`,
      );
      const departmentInputFound = await client.evaluate(`(() => {
        const input = document.querySelector('input[placeholder="engineering, finance"]');
        if (!(input instanceof HTMLInputElement)) return false;
        const setter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )?.set;
        setter?.call(input, "engineering,");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        return true;
      })()`, `${scenario}:department_input_first_token`);
      if (!departmentInputFound) throw new Error("department_input_missing");
      await client.waitFor(
        'document.querySelector(\'input[placeholder="engineering, finance"]\')?.value === "engineering,"',
        `${scenario}:department_trailing_comma_preserved`,
      );
      await client.evaluate(`(() => {
        const input = document.querySelector('input[placeholder="engineering, finance"]');
        const setter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )?.set;
        setter?.call(input, "engineering, finance");
        input.dispatchEvent(new Event("input", { bubbles: true }));
      })()`, `${scenario}:department_input_second_token`);
      await client.waitFor(
        'document.querySelector(\'input[placeholder="engineering, finance"]\')?.value === "engineering, finance"',
        `${scenario}:department_multiple_values_preserved`,
      );

      const roleTrigger = await client.evaluate(`(() => {
        const trigger = document.querySelector("[data-mcp-role-selector-trigger]");
        if (!(trigger instanceof HTMLButtonElement)) return null;
        return {
          role: trigger.getAttribute("role"),
          popup: trigger.getAttribute("aria-haspopup"),
        };
      })()`, `${scenario}:role_trigger_semantics`);
      if (!roleTrigger || roleTrigger.role !== null || roleTrigger.popup !== "dialog") {
        throw new Error(`invalid_role_trigger_semantics:${JSON.stringify(roleTrigger)}`);
      }
      await client.evaluate(
        'document.querySelector("[data-mcp-role-selector-trigger]")?.click()',
        `${scenario}:open_role_selector`,
      );
      await client.waitFor(
        'Boolean(document.querySelector(\'#mcp-role-options[role="dialog"] input[type="checkbox"]\')) && !document.querySelector(\'[role="listbox"], [role="combobox"]\')',
        `${scenario}:role_checkbox_dialog_visible`,
      );
      await client.send("Input.dispatchKeyEvent", {
        type: "keyDown",
        key: "Escape",
        code: "Escape",
      });
      await client.send("Input.dispatchKeyEvent", {
        type: "keyUp",
        key: "Escape",
        code: "Escape",
      });
      await client.waitFor(
        '!document.querySelector(\'#mcp-role-options[role="dialog"]\') && document.activeElement?.hasAttribute("data-mcp-role-selector-trigger")',
        `${scenario}:role_dialog_escape_restores_focus`,
      );
      const sensitiveCanaryVisible = await client.evaluate(`(() => {
        const text = [
          document.body.innerText,
          ...Array.from(document.querySelectorAll("input, textarea"), (element) => element.value),
        ].join("\\n");
        return [
          "mcp-sensitive-endpoint-canary.invalid",
          "MCP_SENSITIVE_HEADER_CANARY",
          "MCP_SENSITIVE_COMMAND_CANARY",
          "MCP_SENSITIVE_ENV_CANARY",
        ].some((value) => text.includes(value));
      })()`, `${scenario}:form_sensitive_canary_scan`);
      if (sensitiveCanaryVisible) throw new Error("sensitive_canary_visible");
      const formScreenshotPath = await writeScreenshot(
        client,
        `${scenario}-form`,
      );
      diagnostic("screenshot_written", {
        case: scenario,
        screenshotPath: formScreenshotPath,
      });
      await client.evaluate(`(() => {
        const values = [
          ['input[placeholder="my-mcp-server"]', "submitted-mcp"],
          ['input[placeholder="engineering, finance"]', "engineering, finance, engineering"],
          ['input[placeholder="https://example.com/mcp"]', "https://submitted.invalid/mcp"],
        ];
        const setter = Object.getOwnPropertyDescriptor(
          HTMLInputElement.prototype,
          "value",
        )?.set;
        for (const [selector, value] of values) {
          const input = document.querySelector(selector);
          if (!(input instanceof HTMLInputElement)) throw new Error("missing_input:" + selector);
          setter?.call(input, value);
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      })()`, `${scenario}:prepare_create_submission`);
      await clickText(client, ["创建服务器"], `${scenario}:submit_create_form`);
      await client.waitFor(
        'Array.isArray(window.__mcpAdminSmoke?.createdBody?.department_ids)',
        `${scenario}:create_body_captured`,
      );
      const submittedDepartments = await client.evaluate(
        'window.__mcpAdminSmoke.createdBody.department_ids',
        `${scenario}:submitted_departments`,
      );
      if (
        JSON.stringify(submittedDepartments) !==
        JSON.stringify(["engineering", "finance"])
      ) {
        throw new Error(
          `invalid_submitted_departments:${JSON.stringify(submittedDepartments)}`,
        );
      }
      return {
        role,
        viewport,
        mode,
        emptyStateScreenshotPath: emptyScreenshotPath,
        formScreenshotPath,
        submittedDepartments,
      };
    }

    const evidence = await client.evaluate(`(() => ({
      adminControls: document.querySelectorAll("[data-mcp-admin-controls]").length,
      adminActions: [
        "添加 MCP 服务器",
        "停用 MCP 服务器",
        "编辑 MCP 服务器",
        "删除 MCP 服务器",
      ].filter((label) =>
        document.querySelector('button[aria-label="' + label + '"]'),
      ).length,
      readyState: Boolean(document.querySelector('[data-mcp-directory-shell][data-frontend-governance-state="ready"]')),
      serverVisible: document.body.innerText.includes(${JSON.stringify(mode === "sandbox-edit" ? "synthetic-sandbox" : "synthetic-mcp")}),
      sensitiveCanaries: [
        "mcp-sensitive-endpoint-canary.invalid",
        "MCP_SENSITIVE_HEADER_CANARY",
        "MCP_SENSITIVE_COMMAND_CANARY",
        "MCP_SENSITIVE_ENV_CANARY",
      ].filter((value) => [
        document.body.innerText,
        ...Array.from(document.querySelectorAll("input, textarea"), (element) => element.value),
      ].some((surface) => surface.includes(value))),
      enabledCount: Array.from(document.body.innerText.matchAll(/已启用/g)).length,
      overflow: document.documentElement.scrollWidth > window.innerWidth,
      text: document.body.innerText,
    }))()`, `${scenario}:ui_evidence`);
    const screenshotPath = await writeScreenshot(client, scenario);
    diagnostic("screenshot_written", { case: scenario, screenshotPath });

    if (!evidence.readyState || !evidence.serverVisible) {
      throw new Error("directory_not_ready");
    }
    if (role === "admin" && (evidence.adminControls !== 2 || evidence.adminActions !== 4)) {
      throw new Error("admin_controls_missing");
    }
    if (role === "user" && (evidence.adminControls !== 0 || evidence.adminActions !== 0)) {
      throw new Error("ordinary_controls_visible");
    }
    if (evidence.sensitiveCanaries.length > 0) {
      throw new Error(`sensitive_canaries_visible:${evidence.sensitiveCanaries.join(",")}`);
    }
    if (evidence.enabledCount > 1) {
      throw new Error(`duplicate_enabled:${evidence.enabledCount}`);
    }
    if (evidence.overflow) {
      throw new Error("viewport_overflow");
    }

    let editRedactionChecked = false;
    if (role === "admin" && !viewport.mobile) {
      await clickAriaLabel(
        client,
        "编辑 MCP 服务器",
        `${scenario}:open_edit_form`,
      );
      await client.waitFor(
        'Boolean(document.querySelector(\'[role="dialog"][aria-modal="true"]\'))',
        `${scenario}:edit_form_visible`,
      );
      const editEvidence = await client.evaluate(`(() => {
        const dialog = document.querySelector('[role="dialog"][aria-modal="true"]');
        if (!dialog) return null;
        const surface = [
          dialog.innerText,
          ...Array.from(dialog.querySelectorAll("input, textarea"), (element) => element.value),
        ].join("\\n");
        const canaries = [
          "mcp-sensitive-endpoint-canary.invalid",
          "MCP_SENSITIVE_HEADER_CANARY",
          "MCP_SENSITIVE_COMMAND_CANARY",
          "MCP_SENSITIVE_ENV_CANARY",
        ].filter((value) => surface.includes(value));
        return {
          canaries,
          hasUrlField: Boolean(dialog.querySelector('input[type="url"]')),
          hasCommandField: Boolean(dialog.querySelector('input[placeholder*="npx"]')),
        };
      })()`, `${scenario}:edit_redaction_values`);
      const expectedField =
        mode === "sandbox-edit"
          ? editEvidence?.hasCommandField
          : editEvidence?.hasUrlField;
      if (!editEvidence || !expectedField || editEvidence.canaries.length > 0) {
        throw new Error(`edit_redaction_failed:${JSON.stringify(editEvidence)}`);
      }
      await client.send("Input.dispatchKeyEvent", {
        type: "keyDown",
        key: "Escape",
        code: "Escape",
      });
      await client.send("Input.dispatchKeyEvent", {
        type: "keyUp",
        key: "Escape",
        code: "Escape",
      });
      await client.waitFor(
        '!document.querySelector(\'[role="dialog"][aria-modal="true"]\')',
        `${scenario}:edit_form_closed`,
      );
      editRedactionChecked = true;
    }

    return {
      role,
      viewport,
      screenshotPath,
      editRedactionChecked,
      ...evidence,
    };
  } catch (error) {
    const pageEvidence = await collectPageEvidence(client, scenario, runtime);
    diagnostic("case_error", {
      case: scenario,
      error: safeError(error),
      evidence: pageEvidence,
    });
    throw new Error(
      `${scenario}\n${safeError(error)}\n${JSON.stringify(pageEvidence, null, 2)}`,
    );
  } finally {
    diagnostic("cleanup", { case: scenario });
    await browser.close();
  }
}

async function main() {
  mkdirSync(outputDir, { recursive: true });
  const vite = await startVite();
  try {
    const roles = process.env.MCP_SMOKE_ROLE
      ? [process.env.MCP_SMOKE_ROLE]
      : ["admin", "user"];
    const viewports =
      process.env.MCP_SMOKE_VIEWPORT === "desktop"
        ? [{ width: 1440, height: 900, mobile: false }]
        : process.env.MCP_SMOKE_VIEWPORT === "mobile"
          ? [{ width: 390, height: 844, mobile: true }]
          : [
              { width: 1440, height: 900, mobile: false },
              { width: 390, height: 844, mobile: true },
            ];
    const results = [];
    if (!process.env.MCP_SMOKE_ROLE && !process.env.MCP_SMOKE_VIEWPORT) {
      results.push(
        await runCase(vite.port, "admin", {
          width: 1440,
          height: 900,
          mobile: false,
        }, "empty"),
      );
      results.push(
        await runCase(
          vite.port,
          "admin",
          { width: 1440, height: 900, mobile: false },
          "sandbox-edit",
        ),
      );
    }
    for (const role of roles) {
      for (const viewport of viewports) {
        results.push(await runCase(vite.port, role, viewport));
      }
    }
    writeFileSync(
      join(outputDir, "evidence.json"),
      JSON.stringify(results, null, 2),
    );
    console.log(JSON.stringify(results, null, 2));
  } finally {
    diagnostic("vite_cleanup");
    await vite.close();
  }
}

main().catch((error) => {
  diagnostic("error", { error: safeError(error) });
  console.error(safeError(error));
  process.exitCode = 1;
});
