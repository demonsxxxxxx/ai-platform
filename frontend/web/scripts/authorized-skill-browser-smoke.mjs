#!/usr/bin/env node
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, join, resolve } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as delay } from "node:timers/promises";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:4173").replace(/\/+$/, "");
const screenshotDir = resolve(
  process.env.AI_PLATFORM_SKILL_SMOKE_SCREENSHOTS ||
    "../../.codex-tmp/authorized-skill-browser-smoke",
);
const outputPath = resolve(
  process.env.AI_PLATFORM_SKILL_SMOKE_OUTPUT ||
    "../../.codex-tmp/authorized-skill-browser-smoke/evidence.json",
);
const timeoutMs = 30_000;

function chromePath() {
  const candidates = [
    process.env.AI_PLATFORM_CHROME_PATH,
    join(process.env.PROGRAMFILES || "", "Google/Chrome/Application/chrome.exe"),
    join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
    join(process.env.PROGRAMFILES || "", "Microsoft/Edge/Application/msedge.exe"),
  ].filter(Boolean);
  return candidates.find((candidate) => {
    try {
      return Boolean(candidate && existsSync(candidate));
    } catch {
      return false;
    }
  });
}

async function httpJson(url, options = {}) {
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
  const executable = chromePath();
  if (!executable) throw new Error("chrome_not_found");
  const port = 9400 + Math.floor(Math.random() * 300);
  const profile = mkdtempSync(join(tmpdir(), "authorized-skill-smoke-"));
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
      await httpJson(`${endpoint}/json/version`);
      break;
    } catch {
      await delay(150);
    }
  }
  const target = await httpJson(
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
        // Chrome may briefly retain profile files on Windows after exit.
      }
    },
  };
}

function mockBootstrapSource() {
  return `(() => {
    localStorage.setItem("language", "en");
    const originalFetch = window.fetch.bind(window);
    const state = window.__authorizedSkillSmoke = {
      submitMode: "stale",
      requests: [],
      errors: [],
      skillListReads: 0,
      staleReturned: false,
    };
    window.addEventListener("error", (event) => state.errors.push(String(event.message)));
    window.addEventListener("unhandledrejection", (event) => state.errors.push(String(event.reason)));
    const json = (value, status = 200) => new Response(JSON.stringify(value), {
      status,
      headers: { "Content-Type": "application/json" },
    });
    const fillerSkills = Array.from({ length: 200 }, (_, index) => ({
      skill_name: "authorized-skill-" + String(index).padStart(3, "0"),
      expected_version: "filler-" + String(index).padStart(3, "0"),
      input_modes: ["chat"],
      requires_file: false,
      description: "Authorized filler Skill " + index,
      tags: ["general"],
      files: ["SKILL.md"],
      enabled: true,
      file_count: 1,
      installed_from: "marketplace",
      is_published: true,
      marketplace_is_active: true,
    }));
    const skills = [...fillerSkills,
      {
        skill_name: "document-review",
        expected_version: "aaaaaaaa11111111",
        input_modes: ["docx"],
        requires_file: true,
        description: "Review an attached document",
        tags: ["document"],
        files: ["SKILL.md"],
        enabled: true,
        file_count: 1,
        installed_from: "marketplace",
        is_published: true,
        marketplace_is_active: true,
      },
    ];
    window.fetch = async (input, init = {}) => {
      const rawUrl = typeof input === "string" ? input : input.url;
      const url = new URL(rawUrl, location.origin);
      const method = String(init.method || (typeof input === "object" && input.method) || "GET").toUpperCase();
      const credentials = init.credentials || (typeof input === "object" && input.credentials) || "same-origin";
      let body = null;
      if (typeof init.body === "string") {
        try { body = JSON.parse(init.body); } catch { body = "unparsed"; }
      }
      if (url.pathname.startsWith("/api/") || url.pathname === "/agents" || url.pathname.startsWith("/mcp")) {
        state.requests.push({ path: url.pathname, method, credentials, body });
      }
      if (url.pathname === "/api/ai/auth/me") return json({
        user_id: "smoke-user",
        user_name: "smoke-user",
        display_name: "Smoke User",
        tenant_id: "tenant-smoke",
        roles: ["member"],
        permissions: ["chat:write", "skill:read", "persona_preset:read"],
        is_admin: false,
        source: "cookie_session",
      });
      if (url.pathname === "/api/agent/models/available") return json({
        models: [{ id: "model-1", value: "mock/model", label: "Mock Model", provider: "mock" }],
        count: 1,
        enabled_count: 1,
        default_model_id: "model-1",
      });
      if (url.pathname === "/api/auth/profile") return json({ metadata: { pinned_model_ids: [] } });
      if (url.pathname === "/agents") return json({
        agents: [{ id: "general-agent", name: "General Agent", description: "General tasks", options: {} }],
        default_agent: "general-agent",
        allowed_model_ids: ["model-1"],
      });
      if (url.pathname === "/api/skills" || url.pathname === "/api/skills/") {
        state.skillListReads += 1;
        const projectedSkills = state.staleReturned
          ? skills.map((skill) => skill.skill_name === "document-review"
              ? { ...skill, expected_version: "cccccccc33333333" }
              : skill)
          : skills;
        const skip = Number(url.searchParams.get("skip") || 0);
        const limit = Number(url.searchParams.get("limit") || 50);
        return json({
          skills: projectedSkills.slice(skip, skip + limit),
          total: projectedSkills.length,
          skip,
          limit,
          available_tags: ["document", "general"],
          effective_permissions: ["skill:read"],
          effective_permissions_known: true,
          catalog_read_resolved: true,
        });
      }
      if (url.pathname === "/api/upload/config") return json({ uploadLimits: {
        image: 10, video: 50, audio: 50, document: 20, maxFiles: 10,
      }});
      if (url.pathname === "/api/upload/check") return json({
        exists: true,
        key: "file-smoke-key",
        name: body?.name || "evidence.txt",
        type: "document",
        mime_type: body?.mime_type || "text/plain",
        size: body?.size || 8,
        url: "/api/upload/file/file-smoke-key",
      });
      if (url.pathname === "/api/chat/stream" && method === "POST") {
        if (state.submitMode === "stale") {
          state.staleReturned = true;
          return json({ detail: "skill_selection_stale" }, 409);
        }
        if (state.submitMode === "denied") {
          return json({ detail: "capability_not_authorized" }, 403);
        }
        return json({ session_id: "session-smoke", run_id: "run-smoke", status: "queued", queue_position: 1 });
      }
      if (url.pathname === "/api/chat/sessions/session-smoke/stream") {
        return new Response("event: done\\ndata: {\\\"run_id\\\":\\\"run-smoke\\\",\\\"type\\\":\\\"done\\\"}\\n\\n", {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        });
      }
      if (url.pathname === "/api/ai/runs/run-smoke/playback") return json({
        contract_version: "v1",
        run_id: "run-smoke",
        run: { run_id: "run-smoke", session_id: "session-smoke", status: "succeeded", progress: 100 },
        timeline: [],
        events: [{ event_id: "event-1", event_type: "completed", message: "Completed", visible_to_user: true }],
        artifacts: [{ artifact_id: "artifact-1", label: "result.txt", artifact_type: "text", content_type: "text/plain", size_bytes: 12, status: "succeeded", preview_url: "/api/artifacts/artifact-1/preview" }],
        steps: [],
        multi_agent: null,
      });
      if (url.pathname.includes("generate-title")) return json({ title: "Authorized Skill task" });
      if (url.pathname.includes("persona") && method === "GET") return json({ presets: [], total: 0, skip: 0, limit: 12, available_tags: [] });
      if (url.pathname.includes("session") && method === "GET") return json({ sessions: [], total: 0, runs: [], events: [] });
      if (url.pathname.startsWith("/mcp") || url.pathname.includes("mcp")) return json({ servers: [] });
      if (url.pathname.startsWith("/api/")) return json({ items: [], total: 0, notifications: [], projects: [] });
      return originalFetch(input, init);
    };
  })();`;
}

async function screenshot(client, name) {
  mkdirSync(screenshotDir, { recursive: true });
  const result = await client.send("Page.captureScreenshot", {
    format: "png",
  });
  const path = join(screenshotDir, `${name}.png`);
  writeFileSync(path, Buffer.from(result.data, "base64"));
  return basename(path);
}

async function setTextarea(client, value) {
  await client.evaluate(`(() => {
    const input = document.querySelector("textarea");
    const descriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
    descriptor.set.call(input, ${JSON.stringify(value)});
    input.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);
}

async function click(client, selector) {
  const clicked = await client.evaluate(`(() => {
    const node = document.querySelector(${JSON.stringify(selector)});
    if (!node) return false;
    node.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`selector_not_found:${selector}`);
}

async function clickText(client, labels) {
  const clicked = await client.evaluate(`(() => {
    const labels = ${JSON.stringify(labels)};
    const node = Array.from(document.querySelectorAll("button")).find((button) =>
      labels.some((label) => button.textContent.trim().includes(label))
    );
    if (!node) return false;
    node.click();
    return true;
  })()`);
  if (!clicked) throw new Error(`button_not_found:${labels.join("|")}`);
}

async function attachFile(client) {
  const filePath = join(tmpdir(), `authorized-skill-${Date.now()}.txt`);
  writeFileSync(filePath, "evidence", "utf8");
  const document = await client.send("DOM.getDocument");
  const query = await client.send("DOM.querySelector", {
    nodeId: document.root.nodeId,
    selector: 'input[type="file"]',
  });
  if (!query.nodeId) throw new Error("file_input_not_found");
  await client.send("DOM.setFileInputFiles", {
    nodeId: query.nodeId,
    files: [filePath],
  });
  await client.evaluate(`(() => {
    const input = document.querySelector('input[type="file"]');
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  })()`);
  return filePath;
}

async function runViewport(name, viewport) {
  const browser = await startBrowser(viewport);
  const { client } = browser;
  let tempFile = null;
  try {
    await client.send("Page.addScriptToEvaluateOnNewDocument", {
      source: mockBootstrapSource(),
    });
    await client.send("Page.navigate", { url: `${baseUrl}/chat` });
    await client.waitFor(
      'Boolean(document.querySelector("[data-librechat-shell]") && document.querySelector("textarea"))',
      `${name}:chat_ready`,
    );

    await setTextarea(client, "$ document-review");
    await client.waitFor(
      'Boolean(document.querySelector("[data-composer-skill-selector]"))',
      `${name}:picker_open`,
    );
    const pickerA11y = await client.evaluate(`(() => {
      const dialog = document.querySelector("[data-composer-skill-selector]");
      const target = document.querySelector('[data-composer-skill-row="document-review"]');
      const targetRect = target?.getBoundingClientRect();
      const buttons = Array.from(dialog?.querySelectorAll("button") || []);
      const byText = (text) => buttons.find((button) => button.textContent.trim().includes(text));
      const height = (node) => node ? Math.round(node.getBoundingClientRect().height) : 0;
      return {
        role: dialog?.getAttribute("role"),
        ariaModal: dialog?.getAttribute("aria-modal"),
        labelled: Boolean(dialog?.getAttribute("aria-labelledby") && document.getElementById(dialog.getAttribute("aria-labelledby"))),
        targetVisible: Boolean(targetRect && targetRect.top >= 0 && targetRect.bottom <= innerHeight),
        touchTargetHeights: {
          close: height(dialog?.querySelector('button[aria-label="Close"], button[aria-label="关闭"]')),
          manage: height(byText("Manage") || byText("管理")),
          done: height(byText("Done") || byText("完成")),
        },
      };
    })()`);
    const pickerScreenshot = await screenshot(client, `${name}-picker-open`);
    await client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape" });
    await client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape" });
    await client.waitFor(
      '!document.querySelector("[data-composer-skill-selector]")',
      `${name}:picker_escape_closed`,
    );
    await client.waitFor(
      'document.activeElement === document.querySelector("textarea")',
      `${name}:picker_focus_restored`,
    );
    const focusRestored = true;
    await click(
      client,
      'button[aria-label^="Open commands"], button[aria-label^="打开命令"]',
    );
    await clickText(client, ["Skills", "技能"]);
    await client.waitFor(
      'Boolean(document.querySelector("[data-composer-skill-selector]"))',
      `${name}:picker_reopened_after_escape`,
    );
    await click(client, '[data-composer-skill-row="document-review"]');
    await client.waitFor(
      'document.querySelector(\'[data-composer-chip-kind="skill"]\')?.getAttribute("data-composer-chip-reference") === "aaaaaaaa"',
      `${name}:skill_selected`,
    );
    await client.waitFor(
      'document.querySelector("textarea").value === ""',
      `${name}:command_draft_cleared`,
    );
    await setTextarea(client, "Review the attached evidence");
    await client.evaluate('document.querySelector("form").requestSubmit()');
    await client.waitFor(
      'Boolean(document.querySelector(\'[data-selected-skill-error="file_required_for_skill"]\'))',
      `${name}:file_required`,
    );
    const requiredScreenshot = await screenshot(client, `${name}-file-required`);
    const selectedDetails = await client.evaluate(`Array.from(
      document.querySelectorAll("[data-composer-skill-visible-detail]")
    ).map((node) => node.textContent.trim())`);
    const selectedSkillRemove = await client.evaluate(`(() => {
      const button = document.querySelector("[data-task-selected-skill-remove]");
      return {
        height: button ? Math.round(button.getBoundingClientRect().height) : 0,
        ariaLabel: button?.getAttribute("aria-label") || "",
      };
    })()`);

    tempFile = await attachFile(client);
    await client.waitFor(
      'document.body.innerText.includes("authorized-skill-") && !document.querySelector(\'[data-selected-skill-error="file_required_for_skill"]\')',
      `${name}:file_attached`,
    );
    await client.evaluate('document.querySelector("form").requestSubmit()');
    await client.waitFor(
      'Boolean(document.querySelector(\'[data-selected-skill-error="skill_selection_stale"]\'))',
      `${name}:stale_visible`,
    );
    const staleState = await client.evaluate(`(() => ({
      prompt: document.querySelector("textarea").value,
      attachmentVisible: document.body.innerText.includes("authorized-skill-"),
      selectedReference: document.querySelector('[data-composer-chip-kind="skill"]')?.getAttribute("data-composer-chip-reference"),
      skillListReads: window.__authorizedSkillSmoke.skillListReads,
    }))()`);
    const staleScreenshot = await screenshot(client, `${name}-stale-preserved`);

    await click(
      client,
      'button[aria-label^="Open commands"], button[aria-label^="打开命令"]',
    );
    await clickText(client, ["Skills", "技能"]);
    await client.waitFor(
      'Boolean(document.querySelector("[data-composer-skill-selector]"))',
      `${name}:picker_reopen`,
    );
    await client.waitFor(
      'document.querySelector(\'[data-composer-skill-row="document-review"] [data-composer-skill-version]\')?.getAttribute("data-composer-skill-version") === "cccccccc33333333"',
      `${name}:current_version_visible`,
    );
    const stalePickerSummary = await client.evaluate(
      'document.querySelector("[data-composer-skill-selection-summary]")?.getAttribute("data-composer-skill-selection-summary")',
    );
    const stalePickerScreenshot = await screenshot(client, `${name}-stale-picker`);
    await click(client, '[data-composer-skill-row="document-review"]');
    await client.evaluate('window.__authorizedSkillSmoke.submitMode = "denied"');
    await client.evaluate('document.querySelector("form").requestSubmit()');
    await client.waitFor(
      'Boolean(document.querySelector(\'[data-selected-skill-error="capability_not_authorized"]\')) && !document.querySelector(\'[data-composer-chip-kind="skill"]\')',
      `${name}:denied_visible`,
    );
    const deniedState = await client.evaluate(`(() => {
      const error = document.querySelector('[data-selected-skill-error="capability_not_authorized"]');
      return {
        prompt: document.querySelector("textarea").value,
        attachmentVisible: document.body.innerText.includes("authorized-skill-"),
        selectedIdentityVisible: Boolean(document.querySelector('[data-composer-chip-kind="skill"]')),
        errorText: error?.textContent.trim() || "",
      };
    })()`);
    const deniedScreenshot = await screenshot(client, `${name}-denied-preserved`);

    await click(
      client,
      'button[aria-label^="Open commands"], button[aria-label^="打开命令"]',
    );
    await clickText(client, ["Skills", "技能"]);
    await client.waitFor(
      'document.querySelector(\'[data-composer-skill-row="document-review"] [data-composer-skill-version]\')?.getAttribute("data-composer-skill-version") === "cccccccc33333333"',
      `${name}:denied_picker_refreshed`,
    );
    await click(client, '[data-composer-skill-row="document-review"]');
    await client.evaluate('window.__authorizedSkillSmoke.submitMode = "success"');
    await client.evaluate('document.querySelector("form").requestSubmit()');
    await client.waitFor(
      'document.querySelector("textarea").value === "" && !document.querySelector(\'[data-composer-chip-kind="skill"]\')',
      `${name}:accepted_clear`,
    );

    await click(client, 'button[title="Menu"]');
    await clickText(client, ["Run playback", "运行回放"]);
    await client.waitFor(
      `(() => {
        const panel = document.querySelector('[data-run-playback-panel][data-run-playback-state="ready"]');
        if (!panel || !panel.innerText.includes("result.txt")) return false;
        const rect = panel.getBoundingClientRect();
        return rect.left < innerWidth && rect.right > 0 && rect.top < innerHeight && rect.bottom > 0;
      })()`,
      `${name}:artifact_entry`,
    );
    await client.waitFor(
      '!document.body.innerText.includes("Queued (position 1)")',
      `${name}:terminal_queue_cleared`,
    );
    await delay(250);
    const artifactState = await client.evaluate(`(() => {
      const panel = document.querySelector('[data-run-playback-panel][data-run-playback-state="ready"]');
      const rect = panel?.getBoundingClientRect();
      return {
        visible: Boolean(rect && rect.width > 0 && rect.height > 0 && rect.left < innerWidth && rect.right > 0 && rect.top < innerHeight && rect.bottom > 0),
        rect: rect ? { left: Math.round(rect.left), top: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) } : null,
        hostRect: panel?.parentElement?.parentElement ? (() => {
          const hostRect = panel.parentElement.parentElement.getBoundingClientRect();
          return { left: Math.round(hostRect.left), top: Math.round(hostRect.top), width: Math.round(hostRect.width), height: Math.round(hostRect.height) };
        })() : null,
        artifactVisible: Boolean(panel?.innerText.includes("result.txt")),
        succeededVisible: Boolean(panel?.innerText.toLowerCase().includes("succeeded") || panel?.innerText.toLowerCase().includes("success")),
        queuedToastVisible: document.body.innerText.includes("Queued (position 1)"),
      };
    })()`);
    const artifactScreenshot = await screenshot(client, `${name}-artifact-entry`);
    const requestEvidence = await client.evaluate(`(() => {
      const requests = window.__authorizedSkillSmoke.requests;
      const submissions = requests.filter((item) => item.path === "/api/chat/stream" && item.method === "POST");
      return {
        submissions,
        credentialViolations: requests
          .filter((item) => item.path.startsWith("/api/") || item.path === "/agents")
          .filter((item) => item.credentials !== "include")
          .map(({ path, method, credentials }) => ({ path, method, credentials })),
        allApiCredentialsIncluded: requests
          .filter((item) => item.path.startsWith("/api/") || item.path === "/agents")
          .every((item) => item.credentials === "include"),
        errors: window.__authorizedSkillSmoke.errors,
      };
    })()`);
    const layout = await client.evaluate(`(() => ({
      viewport: { width: innerWidth, height: innerHeight },
      bodyScrollWidth: document.body.scrollWidth,
      overlaps: Array.from(document.querySelectorAll("[data-composer-skill-selector], [data-selected-skill-error]")).some((node) => {
        const rect = node.getBoundingClientRect();
        return rect.left < 0 || rect.right > innerWidth || rect.top < 0 || rect.bottom > innerHeight;
      }),
    }))()`);

    return {
      name,
      viewport,
      pickerA11y,
      focusRestored,
      selectedDetails,
      selectedSkillRemove,
      staleState,
      stalePickerSummary,
      deniedState,
      artifactState,
      requestEvidence,
      layout,
      screenshots: [
        pickerScreenshot,
        requiredScreenshot,
        staleScreenshot,
        stalePickerScreenshot,
        deniedScreenshot,
        artifactScreenshot,
      ],
    };
  } finally {
    if (tempFile) rmSync(tempFile, { force: true });
    await browser.close();
  }
}

async function main() {
  const results = [];
  results.push(await runViewport("desktop", { width: 1440, height: 1100, mobile: false }));
  results.push(await runViewport("mobile", { width: 390, height: 844, mobile: true }));
  const ok = results.every((result) => {
    const [staleSubmission, deniedSubmission, acceptedSubmission] =
      result.requestEvidence.submissions;
    return (
      result.staleState.prompt === "Review the attached evidence" &&
      result.pickerA11y.role === "dialog" &&
      result.pickerA11y.ariaModal === "true" &&
      result.pickerA11y.labelled === true &&
      result.pickerA11y.targetVisible === true &&
      result.focusRestored === true &&
      result.selectedDetails.includes("vaaaaaaaa") &&
      result.selectedDetails.includes("File required") &&
      result.selectedSkillRemove.ariaLabel.includes("document-review") &&
      (result.name !== "mobile" || result.selectedSkillRemove.height >= 44) &&
      (result.name !== "mobile" || Object.values(result.pickerA11y.touchTargetHeights).every((height) => height >= 44)) &&
      result.staleState.attachmentVisible === true &&
      result.staleState.selectedReference === "aaaaaaaa" &&
      result.staleState.skillListReads >= 4 &&
      result.requestEvidence.allApiCredentialsIncluded === true &&
      result.requestEvidence.errors.length === 0 &&
      staleSubmission?.body?.selected_skill?.skill_id === "document-review" &&
      staleSubmission?.body?.selected_skill?.expected_version === "aaaaaaaa11111111" &&
      staleSubmission?.body?.skill_id === undefined &&
      staleSubmission?.body?.enabled_skills === undefined &&
      staleSubmission?.body?.disabled_skills === undefined &&
      result.stalePickerSummary === "reconfirm" &&
      result.deniedState.prompt === "Review the attached evidence" &&
      result.deniedState.attachmentVisible === true &&
      result.deniedState.selectedIdentityVisible === false &&
      !result.deniedState.errorText.includes("document-review") &&
      deniedSubmission?.body?.selected_skill?.skill_id === "document-review" &&
      deniedSubmission?.body?.selected_skill?.expected_version === "cccccccc33333333" &&
      deniedSubmission?.body?.skill_id === undefined &&
      deniedSubmission?.body?.enabled_skills === undefined &&
      deniedSubmission?.body?.disabled_skills === undefined &&
      acceptedSubmission?.body?.selected_skill?.skill_id === "document-review" &&
      acceptedSubmission?.body?.selected_skill?.expected_version === "cccccccc33333333" &&
      result.artifactState.visible === true &&
      result.artifactState.artifactVisible === true &&
      result.artifactState.succeededVisible === true &&
      result.artifactState.queuedToastVisible === false &&
      result.layout.bodyScrollWidth <= result.layout.viewport.width &&
      result.layout.overlaps === false
    );
  });
  const evidence = {
    schema_version: "ai-platform.authorized-skill-browser-smoke.v1",
    status: ok ? "passed" : "failed",
    base_url: baseUrl,
    generated_at: new Date().toISOString(),
    mock_backed: true,
    credentials_logged: false,
    results,
  };
  mkdirSync(resolve(outputPath, ".."), { recursive: true });
  writeFileSync(outputPath, `${JSON.stringify(evidence, null, 2)}\n`, "utf8");
  console.log(JSON.stringify(evidence, null, 2));
  if (!ok) process.exitCode = 1;
}

main().catch((error) => {
  console.error(JSON.stringify({ status: "failed", error: String(error) }, null, 2));
  process.exit(1);
});
