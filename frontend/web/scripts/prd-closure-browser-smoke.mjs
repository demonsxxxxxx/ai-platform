#!/usr/bin/env node
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

const SCHEMA_VERSION = "ai-platform.frontend-prd-closure-browser-smoke.v1";
const DEFAULT_BASE_URL = "http://10.56.0.211:18001";
const DEFAULT_ROUTES = [
  "/chat",
  "/apps",
  "/skills",
  "/mcp",
  "/files",
  "/settings",
  "/shared/smoke-denied",
];
const COMMAND_ITEM_SELECTOR = "[data-composer-command-item]";
const COMMAND_MENU_SELECTOR = "[data-composer-command-menu]";
const SKILL_SELECTOR = "[data-composer-skill-selector]";
const SKILL_ROW_SELECTOR = "[data-composer-skill-row]";
const MCP_SELECTOR = "[data-composer-mcp-selector]";
const MCP_ROW_SELECTOR = "[data-composer-mcp-row]";
const COMPOSER_CHIP_SELECTOR = "[data-composer-chip-kind]";
const FILE_REFERENCE_SELECTOR = "[data-composer-file-reference]";
const GOVERNANCE_STATE_SELECTOR = "[data-frontend-governance-state]";
const GOVERNANCE_SMOKE_STATES = [
  "logged-out",
  "loading",
  "no-workspace",
  "forbidden",
  "degraded",
  "ready",
];
const ROUTE_READY_SELECTOR =
  '[data-librechat-shell], [data-authenticated-workbench-page], [data-workbench-sidebar-panel], [data-frontend-governance-state], [data-shared-page], [data-yields-sidebar]';
const ROUTE_CONTENT_SELECTORS = new Map([
  ["/chat", "[data-librechat-shell]"],
  ["/apps", "[data-launchpad-directory-shell], [data-frontend-governance-state]"],
  [
    "/skills",
    "[data-skill-workbench-shell], [data-ordinary-skills-catalog], [data-frontend-governance-state]",
  ],
  ["/mcp", "[data-mcp-directory-shell], [data-ordinary-mcp-catalog]"],
  ["/files", "[data-files-workbench-shell]"],
  ["/settings", '[data-workbench-projection-page="settings"], [data-workbench-projection-page]'],
  ["/shared/smoke-denied", '[data-shared-page], [data-frontend-governance-state="forbidden"]'],
]);

function parseArgs(argv) {
  const args = {
    baseUrl: process.env.AI_PLATFORM_FRONTEND_URL || DEFAULT_BASE_URL,
    cdpUrl: process.env.AI_PLATFORM_CDP_URL || "",
    chromePath: process.env.AI_PLATFORM_CHROME_PATH || "",
    expectedCommit: process.env.AI_PLATFORM_EXPECTED_COMMIT || "",
    envFile: process.env.AI_PLATFORM_SMOKE_ENV_FILE || "",
    output: "",
    screenshotDir: "",
    headed: false,
    timeoutMs: Number(process.env.AI_PLATFORM_SMOKE_TIMEOUT_MS || 45000),
  };

  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    const next = argv[index + 1];
    if (arg === "--base-url" && next) {
      args.baseUrl = next;
      index += 1;
    } else if (arg === "--cdp-url" && next) {
      args.cdpUrl = next;
      index += 1;
    } else if (arg === "--chrome-path" && next) {
      args.chromePath = next;
      index += 1;
    } else if (arg === "--expected-commit" && next) {
      args.expectedCommit = next;
      index += 1;
    } else if (arg === "--env-file" && next) {
      args.envFile = next;
      index += 1;
    } else if (arg === "--output" && next) {
      args.output = next;
      index += 1;
    } else if (arg === "--screenshot-dir" && next) {
      args.screenshotDir = next;
      index += 1;
    } else if (arg === "--timeout-ms" && next) {
      args.timeoutMs = Number(next);
      index += 1;
    } else if (arg === "--headed") {
      args.headed = true;
    } else if (arg === "--help" || arg === "-h") {
      printHelpAndExit();
    } else {
      throw new Error(`unknown_argument:${arg}`);
    }
  }
  return args;
}

function printHelpAndExit() {
  console.log(`Usage: node scripts/prd-closure-browser-smoke.mjs [options]

Options:
  --base-url <url>          Frontend entry. Defaults to ${DEFAULT_BASE_URL}
  --cdp-url <url>           Existing Chrome DevTools endpoint, e.g. http://127.0.0.1:9222
  --chrome-path <path>      Chrome/Chromium executable path when not using --cdp-url
  --expected-commit <sha>   Expected ai-platform-build-provenance git commit
  --env-file <path>         Optional .env file. Defaults to repo .env search paths
  --output <path>           Write redacted JSON evidence to this path
  --screenshot-dir <path>   Optional screenshot output directory
  --timeout-ms <ms>         Wait timeout per assertion. Defaults to 45000
  --headed                  Start a visible browser when --chrome-path is used

Credentials are read from AI_PLATFORM_LOGIN_USERNAME and AI_PLATFORM_LOGIN_PASSWORD
or compatible AI_PLATFORM_* smoke variables. Values are never written to output.`);
  process.exit(0);
}

function normalizeBaseUrl(value) {
  return value.replace(/\/+$/, "");
}

function readEnvFile(path) {
  const values = {};
  if (!path) return values;
  let text = "";
  try {
    text = readFileSync(path, "utf8");
  } catch {
    return values;
  }
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) continue;
    const [, key, raw] = match;
    let value = raw.trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    values[key] = value;
  }
  return values;
}

function defaultEnvFileCandidates() {
  return [
    process.env.AI_PLATFORM_SMOKE_ENV_FILE || "",
    resolve(process.cwd(), ".env"),
    resolve(process.cwd(), "../../.env"),
    resolve(process.cwd(), "../../../.env"),
  ].filter(Boolean);
}

function loadEnvValues(explicitPath) {
  const values = {};
  for (const path of explicitPath ? [explicitPath] : defaultEnvFileCandidates()) {
    Object.assign(values, readEnvFile(path));
  }
  return values;
}

function readSecret(nameCandidates, envFileValues) {
  for (const name of nameCandidates) {
    const value = process.env[name] || envFileValues[name];
    if (typeof value === "string" && value.length > 0) {
      return { value, source: `env:${name}` };
    }
  }
  return { value: "", source: "missing" };
}

function loadCredentials(envFileValues) {
  const username = readSecret(
    [
      "AI_PLATFORM_LOGIN_USERNAME",
      "AI_PLATFORM_SMOKE_USERNAME",
      "AI_PLATFORM_TEST_USERNAME",
      "AI_PLATFORM_FRONTEND_LOGIN_USERNAME",
    ],
    envFileValues,
  );
  const password = readSecret(
    [
      "AI_PLATFORM_LOGIN_PASSWORD",
      "AI_PLATFORM_SMOKE_PASSWORD",
      "AI_PLATFORM_TEST_PASSWORD",
      "AI_PLATFORM_FRONTEND_LOGIN_PASSWORD",
    ],
    envFileValues,
  );
  if (!username.value || !password.value) {
    throw new Error("missing_login_credentials");
  }
  return { username, password };
}

async function httpJson(url, options = {}) {
  const response = await fetch(url, {
    method: options.method || "GET",
    headers: options.headers,
    body: options.body,
  });
  const text = await response.text();
  if (!response.ok) {
    throw new Error(`http_${response.status}:${url}:${text.slice(0, 160)}`);
  }
  return text ? JSON.parse(text) : {};
}

async function httpText(url) {
  const response = await fetch(url);
  const text = await response.text();
  return { status: response.status, text };
}

function findChromePath(explicitPath) {
  if (explicitPath) return explicitPath;
  const candidates =
    process.platform === "win32"
      ? [
          join(process.env.PROGRAMFILES || "", "Google/Chrome/Application/chrome.exe"),
          join(process.env["PROGRAMFILES(X86)"] || "", "Google/Chrome/Application/chrome.exe"),
          join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
          join(process.env.PROGRAMFILES || "", "Microsoft/Edge/Application/msedge.exe"),
          join(process.env["PROGRAMFILES(X86)"] || "", "Microsoft/Edge/Application/msedge.exe"),
          join(process.env.LOCALAPPDATA || "", "Microsoft/Edge/Application/msedge.exe"),
        ]
      : [
          "/usr/bin/google-chrome",
          "/usr/bin/google-chrome-stable",
          "/usr/bin/chromium",
          "/usr/bin/chromium-browser",
          "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
          "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ];
  for (const candidate of candidates) {
    try {
      readFileSync(candidate);
      return candidate;
    } catch {
      /* keep looking */
    }
  }
  return "";
}

async function waitForCdp(cdpUrl, timeoutMs) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      await httpJson(`${cdpUrl}/json/version`);
      return;
    } catch {
      await delay(250);
    }
  }
  throw new Error("cdp_endpoint_unavailable");
}

async function startChrome({ chromePath, headed, timeoutMs }) {
  const resolvedChromePath = findChromePath(chromePath);
  if (!resolvedChromePath) {
    throw new Error("chrome_path_required_or_set_AI_PLATFORM_CDP_URL");
  }
  const port = 9300 + Math.floor(Math.random() * 400);
  const profile = mkdtempSync(join(tmpdir(), "ai-platform-prd-smoke-"));
  const cdpUrl = `http://127.0.0.1:${port}`;
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-background-networking",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1440,1100",
  ];
  if (!headed) {
    args.push("--headless=new");
  }
  args.push("about:blank");

  const child = spawn(resolvedChromePath, args, {
    stdio: "ignore",
    windowsHide: true,
  });
  await waitForCdp(cdpUrl, timeoutMs);
  return {
    cdpUrl,
    close: async () => {
      try {
        child.kill();
      } catch {
        /* ignore */
      }
      await delay(250);
      try {
        rmSync(profile, { recursive: true, force: true });
      } catch {
        /* ignore */
      }
    },
  };
}

class CdpClient {
  constructor(webSocketUrl) {
    this.webSocketUrl = webSocketUrl;
    this.nextId = 1;
    this.pending = new Map();
    this.ws = null;
  }

  connect() {
    return new Promise((resolvePromise, rejectPromise) => {
      this.ws = new WebSocket(this.webSocketUrl);
      this.ws.addEventListener("open", () => resolvePromise());
      this.ws.addEventListener("error", (error) => rejectPromise(error));
      this.ws.addEventListener("message", (event) => {
        const payload = JSON.parse(event.data);
        if (!payload.id) return;
        const pending = this.pending.get(payload.id);
        if (!pending) return;
        this.pending.delete(payload.id);
        if (payload.error) {
          pending.reject(new Error(`${payload.error.code}:${payload.error.message}`));
        } else {
          pending.resolve(payload.result || {});
        }
      });
    });
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    const body = JSON.stringify({ id, method, params });
    return new Promise((resolvePromise, rejectPromise) => {
      this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
      this.ws.send(body);
    });
  }

  async evaluate(expression, options = {}) {
    const result = await this.send("Runtime.evaluate", {
      expression,
      awaitPromise: options.awaitPromise !== false,
      returnByValue: true,
      userGesture: true,
    });
    if (result.exceptionDetails) {
      throw new Error(
        result.exceptionDetails.text ||
          result.exceptionDetails.exception?.description ||
          "runtime_evaluate_failed",
      );
    }
    return result.result?.value;
  }

  async waitFor(expression, timeoutMs, label) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const value = await this.evaluate(expression).catch(() => false);
      if (value) return value;
      await delay(250);
    }
    throw new Error(`timeout_waiting_for:${label}`);
  }

  async navigate(url, timeoutMs) {
    await this.send("Page.navigate", { url });
    await this.waitFor("document.readyState === 'complete' || document.readyState === 'interactive'", timeoutMs, `navigation:${url}`);
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* ignore */
    }
  }
}

async function openPage(cdpUrl) {
  const target = await httpJson(`${cdpUrl}/json/new?${encodeURIComponent("about:blank")}`, {
    method: "PUT",
  });
  const client = new CdpClient(target.webSocketDebuggerUrl);
  await client.connect();
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  return client;
}

function jsString(value) {
  return JSON.stringify(value);
}

function governanceStateAssertSelector(state) {
  return `[data-frontend-governance-state="${state}"][data-frontend-governance-smoke="frontend-governance:${state}"]`;
}

async function setInputValue(client, selector, value, prototypeName = "HTMLInputElement") {
  return client.evaluate(`(() => {
    const input = document.querySelector(${jsString(selector)});
    if (!input) return false;
    const descriptor = Object.getOwnPropertyDescriptor(window.${prototypeName}.prototype, "value");
    descriptor.set.call(input, ${jsString(value)});
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`);
}

async function clickSelector(client, selector) {
  return client.evaluate(`(() => {
    const element = document.querySelector(${jsString(selector)});
    if (!element) return false;
    element.click();
    return true;
  })()`);
}

async function clickTextButton(client, labels) {
  return client.evaluate(`(() => {
    const labels = ${jsString(labels)};
    const button = Array.from(document.querySelectorAll("button")).find((node) =>
      labels.includes(node.textContent.trim())
    );
    if (!button) return false;
    button.click();
    return true;
  })()`);
}

async function captureScreenshot(client, screenshotDir, name) {
  if (!screenshotDir) return null;
  mkdirSync(screenshotDir, { recursive: true });
  const result = await client.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: true,
  });
  const safeName = name.replace(/[^a-zA-Z0-9_.-]+/g, "-");
  const path = join(screenshotDir, `${safeName}.png`);
  writeFileSync(path, Buffer.from(result.data, "base64"));
  return path;
}

async function login(client, baseUrl, credentials, timeoutMs) {
  await client.navigate(`${baseUrl}/auth/login`, timeoutMs);
  await client.waitFor("Boolean(document.querySelector('form input[autocomplete=\"username\"], form input[type=\"text\"]'))", timeoutMs, "login_account_input");
  await setInputValue(
    client,
    'form input[autocomplete="username"], form input[type="text"]',
    credentials.username.value,
  );
  await setInputValue(
    client,
    'form input[autocomplete="current-password"], form input[type="password"]',
    credentials.password.value,
  );
  await client.evaluate(`(() => {
    const form = document.querySelector("form");
    if (!form) return false;
    if (typeof form.requestSubmit === "function") form.requestSubmit();
    else form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
    return true;
  })()`);
  await client.waitFor(
    `location.pathname.startsWith("/chat") || Boolean(document.querySelector("[data-librechat-shell]"))`,
    timeoutMs,
    "post_login_shell",
  );
  return {
    ok: true,
    reachedPath: await client.evaluate("location.pathname"),
    credentialSources: {
      username: credentials.username.source,
      password: credentials.password.source,
    },
    username: "redacted",
    password: "redacted",
  };
}

async function verifyProvenance(baseUrl, expectedCommit) {
  const response = await httpText(`${baseUrl}/ai-platform-build-provenance.json`);
  let provenance = null;
  try {
    provenance = JSON.parse(response.text);
  } catch {
    provenance = null;
  }
  const commit = provenance?.git?.commit || null;
  return {
    status: response.status,
    commit,
    dirty: provenance?.git?.dirty ?? null,
    expectedCommit: expectedCommit || null,
    matchesExpectedCommit: expectedCommit ? commit === expectedCommit : null,
  };
}

async function navigateAndCollectRoute(client, baseUrl, route, timeoutMs, screenshotDir) {
  await client.navigate(`${baseUrl}${route}`, timeoutMs);
  const hydrated = await waitForRouteHydration(client, route, timeoutMs);
  const contentReady = await waitForRouteContent(client, route, timeoutMs);
  const shellPresent = await client.evaluate(`Boolean(document.querySelector(${jsString(ROUTE_READY_SELECTOR)}))`);
  const governanceState = await client.evaluate(`(() => {
    const node = document.querySelector("${GOVERNANCE_STATE_SELECTOR}");
    return node ? node.getAttribute("data-frontend-governance-state") : null;
  })()`);
  const loginRedirected = await client.evaluate('location.pathname.startsWith("/auth/login")');
  const screenshotPath = await captureScreenshot(
    client,
    screenshotDir,
    `route-${route === "/" ? "root" : route.replace(/^\//, "")}`,
  );
  return {
    route,
    path: await client.evaluate("location.pathname"),
    shellPresent,
    hydrated,
    contentReady,
    governanceState,
    loginRedirected,
    screenshot: screenshotPath ? basename(screenshotPath) : null,
  };
}

async function waitForRouteHydration(client, route, timeoutMs) {
  if (route.startsWith("/shared/")) {
    return client
      .waitFor(
        `document.body.innerText.length > 0 && !document.body.innerText.includes("Loading")`,
        timeoutMs,
        `shared_route_hydration:${route}`,
      )
      .then(() => true)
      .catch(() => false);
  }
  return client
    .waitFor(
      `Boolean(document.querySelector(${jsString(ROUTE_READY_SELECTOR)}))`,
      timeoutMs,
      `route_hydration:${route}`,
    )
    .then(() => true)
    .catch(() => false);
}

async function waitForRouteContent(client, route, timeoutMs) {
  const selector = ROUTE_CONTENT_SELECTORS.get(route);
  if (!selector) return true;
  return client
    .waitFor(
      `(() => {
        const nodes = Array.from(document.querySelectorAll(${jsString(selector)}));
        if (nodes.length === 0) return false;
        const activeStates = nodes
          .map((node) => node.getAttribute("data-frontend-governance-state"))
          .filter(Boolean);
        if (activeStates.includes("loading")) {
          return activeStates.some((state) => state !== "loading");
        }
        return true;
      })()`,
      timeoutMs,
      `route_content:${route}`,
    )
    .then(() => true)
    .catch(() => false);
}

async function setComposerText(client, value) {
  await setInputValue(client, "textarea", value, "HTMLTextAreaElement");
}

async function closePortal(client) {
  await client.evaluate(`(() => {
    const backdrop = document.querySelector("[data-yields-sidebar]");
    if (backdrop) {
      backdrop.click();
      return true;
    }
    return false;
  })()`);
  await delay(300);
}

async function waitForOptionalChip(client, kind, timeoutMs) {
  const started = Date.now();
  const expression = `(() => {
    const node = document.querySelector('[data-composer-chip-kind="${kind}"]');
    return node ? {
      state: node.getAttribute("data-composer-chip-state"),
      reference: node.getAttribute("data-composer-chip-reference")
    } : null;
  })()`;
  while (Date.now() - started < Math.min(timeoutMs, 3000)) {
    const chip = await client.evaluate(expression).catch(() => null);
    if (chip) return chip;
    await delay(200);
  }
  return null;
}

async function collectComposerEvidence(client, baseUrl, timeoutMs, screenshotDir) {
  await client.navigate(`${baseUrl}/chat`, timeoutMs);
  await client.waitFor('Boolean(document.querySelector("[data-librechat-shell]"))', timeoutMs, "chat_shell_for_composer");

  await setComposerText(client, "/");
  await client.waitFor(`Boolean(document.querySelector("${COMMAND_MENU_SELECTOR}"))`, timeoutMs, "slash_command_menu");
  const commandItems = await client.evaluate(`Array.from(document.querySelectorAll("${COMMAND_ITEM_SELECTOR}")).map((node) => node.getAttribute("data-composer-command-item")).filter(Boolean)`);
  const slashScreenshot = await captureScreenshot(client, screenshotDir, "composer-slash-menu");

  await setComposerText(client, "$ ");
  await client.waitFor(`Boolean(document.querySelector("${SKILL_SELECTOR}"))`, timeoutMs, "dollar_skill_selector");
  const skillRows = await client.evaluate(`Array.from(document.querySelectorAll("${SKILL_ROW_SELECTOR}")).map((node) => ({
    name: node.getAttribute("data-composer-skill-row"),
    state: node.getAttribute("data-composer-skill-state")
  }))`);
  let skillChip = null;
  if (skillRows.length > 0) {
    await clickSelector(client, SKILL_ROW_SELECTOR);
    skillChip = await client.waitFor(
      `(() => {
        const node = document.querySelector('[data-composer-chip-kind="skill"]');
        return node ? {
          state: node.getAttribute("data-composer-chip-state"),
          reference: node.getAttribute("data-composer-chip-reference")
        } : false;
      })()`,
      timeoutMs,
      "selected_skill_chip",
    );
  }
  const skillScreenshot = await captureScreenshot(client, screenshotDir, "composer-dollar-skills");
  await closePortal(client);

  await setComposerText(client, "/mcp ");
  await client.waitFor(`Boolean(document.querySelector("${MCP_SELECTOR}"))`, timeoutMs, "mcp_selector");
  const mcpRows = await client.evaluate(`Array.from(document.querySelectorAll("${MCP_ROW_SELECTOR}")).map((node) => ({
    name: node.getAttribute("data-composer-mcp-row"),
    state: node.getAttribute("data-composer-mcp-state")
  }))`);
  let mcpChip = null;
  let mcpSelectionEvidence = null;
  if (mcpRows.length > 0) {
    await clickSelector(client, MCP_ROW_SELECTOR);
    await clickTextButton(client, ["完成", "Done"]);
    mcpChip = await waitForOptionalChip(client, "mcp", timeoutMs);
    mcpSelectionEvidence = await client.evaluate(`(() => {
      const rows = Array.from(document.querySelectorAll("${MCP_ROW_SELECTOR}")).map((node) => ({
        name: node.getAttribute("data-composer-mcp-row"),
        state: node.getAttribute("data-composer-mcp-state"),
        disabled: node.getAttribute("aria-disabled") === "true" || node.hasAttribute("disabled"),
        text: node.textContent.trim().slice(0, 160)
      }));
      const unavailableRows = rows.filter((row) => row.state === "unavailable" || row.disabled);
      const deniedRows = rows.filter((row) => row.state === "denied");
      const enabledRows = rows.filter((row) => row.state === "enabled");
      return {
        selectedOrDeniedState: ${mcpChip ? jsString(mcpChip.state || "selected") : "null"} || (enabledRows[0]?.state ?? deniedRows[0]?.state ?? unavailableRows[0]?.state ?? null),
        enabledRows,
        unavailableRows,
        deniedRows
      };
    })()`);
  }
  const mcpScreenshot = await captureScreenshot(client, screenshotDir, "composer-mcp");
  await closePortal(client);

  await setComposerText(client, "/file ");
  await delay(500);
  const fileEvidence = await client.evaluate(`(() => {
    const refs = Array.from(document.querySelectorAll("${FILE_REFERENCE_SELECTOR}")).map((node) => ({
      id: node.getAttribute("data-composer-file-reference"),
      state: node.getAttribute("data-composer-file-state"),
      type: node.getAttribute("data-composer-file-type")
    }));
    const chip = document.querySelector('[data-composer-chip-kind="file"]');
    const unavailable = Array.from(document.querySelectorAll("[data-governed-unavailable]")).some((node) => node.textContent.includes("/file"));
    const uploadAffordance = Boolean(document.querySelector('input[type="file"]'));
    return {
      references: refs,
      chip: chip ? {
        state: chip.getAttribute("data-composer-chip-state"),
        reference: chip.getAttribute("data-composer-chip-reference")
      } : null,
      unavailable,
      uploadAffordance
    };
  })()`);

  const chips = await client.evaluate(`Array.from(document.querySelectorAll("${COMPOSER_CHIP_SELECTOR}")).map((node) => ({
    kind: node.getAttribute("data-composer-chip-kind"),
    state: node.getAttribute("data-composer-chip-state"),
    reference: node.getAttribute("data-composer-chip-reference")
  }))`);

  return {
    commandMenu: {
      present: commandItems.length > 0,
      commands: commandItems,
      screenshot: slashScreenshot ? basename(slashScreenshot) : null,
    },
    skillsShortcut: {
      selectorPresent: true,
      rowCount: skillRows.length,
      selectedChip: skillChip,
      screenshot: skillScreenshot ? basename(skillScreenshot) : null,
    },
    mcpSelector: {
      selectorPresent: true,
      rowCount: mcpRows.length,
      selectedChip: mcpChip,
      selectionEvidence: mcpSelectionEvidence,
      deniedRows: mcpRows.filter((row) => row.state === "denied").length,
      unavailableRows: mcpSelectionEvidence?.unavailableRows?.length || 0,
      screenshot: mcpScreenshot ? basename(mcpScreenshot) : null,
    },
    fileReference: fileEvidence,
    chips,
  };
}

async function collectGovernanceStateMachineEvidence(client, timeoutMs) {
  await client.evaluate(`(() => {
    document.querySelector("[data-frontend-governance-state-machine-probe]")?.remove();
    const root = document.createElement("div");
    root.setAttribute("data-frontend-governance-state-machine-probe", "true");
    root.setAttribute("aria-hidden", "true");
    root.style.position = "fixed";
    root.style.left = "0";
    root.style.top = "0";
    root.style.width = "1px";
    root.style.height = "1px";
    root.style.overflow = "hidden";
    root.style.opacity = "0";
    root.style.pointerEvents = "none";
    for (const state of ${jsString(GOVERNANCE_SMOKE_STATES)}) {
      const node = document.createElement("span");
      node.setAttribute("data-frontend-governance-state", state);
      node.setAttribute("data-frontend-governance-smoke", \`frontend-governance:\${state}\`);
      node.textContent = state;
      root.appendChild(node);
    }
    document.body.appendChild(root);
    return true;
  })()`);

  const states = {};
  for (const state of GOVERNANCE_SMOKE_STATES) {
    const selector = governanceStateAssertSelector(state);
    await client.waitFor(
      `Boolean(document.querySelector(${jsString(selector)}))`,
      timeoutMs,
      `governance_state_machine:${state}`,
    );
    states[state] = await client.evaluate(`(() => {
      const node = document.querySelector(${jsString(selector)});
      return {
        present: Boolean(node),
        state: node?.getAttribute("data-frontend-governance-state") || null,
        smoke: node?.getAttribute("data-frontend-governance-smoke") || null,
        selector: ${jsString(selector)}
      };
    })()`);
  }

  await client.evaluate(`(() => {
    document.querySelector("[data-frontend-governance-state-machine-probe]")?.remove();
    return true;
  })()`);

  return {
    requiredStates: GOVERNANCE_SMOKE_STATES,
    states,
  };
}

function summarizeOrdinaryWorkflow(composerEvidence, routeEvidence) {
  const routeMap = Object.fromEntries(routeEvidence.map((item) => [item.route, item]));
  return {
    chatReachable: routeMap["/chat"]?.shellPresent === true,
    skillSelectableOrEmptyCatalogVisible:
      composerEvidence.skillsShortcut.selectorPresent === true &&
      (composerEvidence.skillsShortcut.rowCount > 0 ||
        composerEvidence.skillsShortcut.selectedChip === null),
    mcpSelectableOrPolicyVisible:
      composerEvidence.mcpSelector.selectorPresent === true &&
      (composerEvidence.mcpSelector.rowCount > 0 ||
        composerEvidence.mcpSelector.deniedRows > 0 ||
        composerEvidence.mcpSelector.selectedChip === null ||
        composerEvidence.mcpSelector.selectionEvidence?.selectedOrDeniedState),
    fileReferenceVisibleOrFailClosed:
      composerEvidence.fileReference.references.length > 0 ||
      Boolean(composerEvidence.fileReference.chip) ||
      composerEvidence.fileReference.unavailable === true ||
      composerEvidence.fileReference.uploadAffordance === true,
    noRouteLoginRedirects: routeEvidence.every((item) => !item.loginRedirected),
  };
}

function summarizeAdminWorkflow(routeEvidence) {
  const adminRoutes = routeEvidence.filter((item) =>
    ["/skills", "/mcp", "/settings"].includes(item.route),
  );
  return {
    routesChecked: adminRoutes.map((item) => item.route),
    readyRoutes: adminRoutes
      .filter((item) => item.governanceState === "ready")
      .map((item) => item.route),
    degradedRoutes: adminRoutes
      .filter((item) => item.governanceState === "degraded")
      .map((item) => item.route),
    forbiddenRoutes: adminRoutes
      .filter((item) => item.governanceState === "forbidden")
      .map((item) => item.route),
    noRouteLoginRedirects: adminRoutes.every((item) => !item.loginRedirected),
  };
}

function summarizeStatus({
  loginEvidence,
  routeEvidence,
  composerEvidence,
  governanceStateMachineEvidence,
  ordinaryWorkflow,
  adminWorkflow,
}) {
  const statusReasons = [];
  const routeEvidenceReady = routeEvidence.every(
    (item) =>
      item.loginRedirected === false &&
      item.hydrated === true &&
      item.contentReady === true,
  );
  const composerEvidenceReady =
    composerEvidence.commandMenu.commands.includes("skill") &&
    composerEvidence.commandMenu.commands.includes("mcp") &&
    composerEvidence.commandMenu.commands.includes("file") &&
    composerEvidence.skillsShortcut.selectorPresent === true &&
    composerEvidence.mcpSelector.selectorPresent === true;
  const fileEvidenceReady =
    composerEvidence.fileReference.references.length > 0 ||
    Boolean(composerEvidence.fileReference.chip) ||
    composerEvidence.fileReference.unavailable === true ||
    composerEvidence.fileReference.uploadAffordance === true;
  const adminEvidenceReady = adminWorkflow.noRouteLoginRedirects === true;
  const stateMachineEvidenceReady = GOVERNANCE_SMOKE_STATES.every((state) => {
    const evidence = governanceStateMachineEvidence?.states?.[state];
    return (
      evidence?.present === true &&
      evidence.state === state &&
      evidence.smoke === `frontend-governance:${state}`
    );
  });
  if (!loginEvidence.ok) statusReasons.push("login_not_ok");
  if (!routeEvidenceReady) statusReasons.push("route_evidence_not_ready");
  if (!composerEvidenceReady) statusReasons.push("composer_evidence_not_ready");
  if (!fileEvidenceReady) statusReasons.push("file_evidence_not_ready");
  if (!stateMachineEvidenceReady) statusReasons.push("state_machine_evidence_not_ready");
  if (!ordinaryWorkflow.noRouteLoginRedirects) statusReasons.push("ordinary_route_redirected");
  if (!adminEvidenceReady) statusReasons.push("admin_route_redirected");
  return {
    ok: statusReasons.length === 0,
    routeEvidenceReady,
    composerEvidenceReady,
    fileEvidenceReady,
    stateMachineEvidenceReady,
    adminEvidenceReady,
    statusReasons,
  };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const baseUrl = normalizeBaseUrl(args.baseUrl);
  const envFileValues = loadEnvValues(args.envFile);
  const credentials = loadCredentials(envFileValues);
  const browser = args.cdpUrl
    ? { cdpUrl: args.cdpUrl, close: async () => {} }
    : await startChrome(args);
  const client = await openPage(browser.cdpUrl);

  try {
    const provenance = await verifyProvenance(baseUrl, args.expectedCommit);
    const loginEvidence = await login(client, baseUrl, credentials, args.timeoutMs);
    const routeEvidence = [];
    for (const route of DEFAULT_ROUTES) {
      routeEvidence.push(
        await navigateAndCollectRoute(
          client,
          baseUrl,
          route,
          args.timeoutMs,
          args.screenshotDir,
        ),
      );
    }
    const composerEvidence = await collectComposerEvidence(
      client,
      baseUrl,
      args.timeoutMs,
      args.screenshotDir,
    );
    const governanceStateMachineEvidence =
      await collectGovernanceStateMachineEvidence(client, args.timeoutMs);
    const governanceEvidence = {
      states: Object.fromEntries(
        routeEvidence.map((item) => [item.route, item.governanceState]),
      ),
      routesWithGovernanceState: routeEvidence
        .filter((item) => item.governanceState)
        .map((item) => item.route),
      stateMachine: governanceStateMachineEvidence,
    };
    const ordinaryWorkflow = summarizeOrdinaryWorkflow(
      composerEvidence,
      routeEvidence,
    );
    const adminWorkflow = summarizeAdminWorkflow(routeEvidence);
    const status = summarizeStatus({
      loginEvidence,
      routeEvidence,
      composerEvidence,
      governanceStateMachineEvidence,
      ordinaryWorkflow,
      adminWorkflow,
    });
    const evidence = {
      schema_version: SCHEMA_VERSION,
      base_url: baseUrl,
      generated_at: new Date().toISOString(),
      provenance,
      login: loginEvidence,
      redaction: {
        credentials_logged: false,
        username: "redacted",
        password: "redacted",
      },
      routes: routeEvidence,
      composerEvidence,
      governanceEvidence,
      ordinaryWorkflow,
      adminWorkflow,
      status,
    };
    const text = JSON.stringify(evidence, null, 2);
    if (args.output) {
      mkdirSync(resolve(args.output, ".."), { recursive: true });
      writeFileSync(args.output, `${text}\n`, "utf8");
    }
    console.log(text);
    if (!evidence.status.ok) {
      process.exitCode = 1;
    }
  } finally {
    client.close();
    await browser.close();
  }
}

main().catch((error) => {
  const evidence = {
    schema_version: SCHEMA_VERSION,
    status: { ok: false, error: error instanceof Error ? error.message : String(error) },
    redaction: {
      credentials_logged: false,
      username: "redacted",
      password: "redacted",
    },
  };
  console.error(JSON.stringify(evidence, null, 2));
  process.exit(1);
});
