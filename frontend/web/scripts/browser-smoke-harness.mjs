import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const defaultTimeoutMs = 30_000;

export { sleep };

function redactText(value, privateValues) {
  let text = String(value);
  for (const privateValue of privateValues) {
    if (!privateValue) continue;
    text = text.replaceAll(privateValue, "[REDACTED]");
    text = text.replaceAll(JSON.stringify(privateValue).slice(1, -1), "[REDACTED]");
  }
  return text.replace(
    /((?:authorization|cookie|password|secret|token)\s*[:=]\s*)[^\s,;]+/gi,
    "$1[REDACTED]",
  );
}

/** Redacts known private values and credential-shaped diagnostic fragments. */
export function redact(value, privateValues = []) {
  return redactText(typeof value === "string" ? value : JSON.stringify(value), privateValues);
}

/** Returns a redacted diagnostic value without leaking private object fields. */
export function redactedValue(value, privateValues = []) {
  if (Array.isArray(value)) {
    return value.map((item) => redactedValue(item, privateValues));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        /authorization|cookie|password|secret|token/i.test(key)
          ? "[REDACTED]"
          : redactedValue(item, privateValues),
      ]),
    );
  }
  return typeof value === "string" ? redactText(value, privateValues) : value;
}

/** Returns an explicitly configured Chrome/Edge executable when one is available. */
export function findChrome(explicitPath = process.env.AI_PLATFORM_CHROME_PATH) {
  const candidates = [
    explicitPath,
    join(process.env.PROGRAMFILES || "", "Google/Chrome/Application/chrome.exe"),
    join(process.env.LOCALAPPDATA || "", "Google/Chrome/Application/chrome.exe"),
    join(process.env.PROGRAMFILES || "", "Microsoft/Edge/Application/msedge.exe"),
  ].filter(Boolean);
  return candidates.find((candidate) => existsSync(candidate));
}

/** Rejects an asynchronous CDP operation when it exceeds its scenario timeout. */
export async function withTimeout(promise, timeoutMs, label) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, rejectPromise) => {
        timer = setTimeout(() => rejectPromise(new Error(`timeout:${label}`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

/** Reads a successful JSON response from a local browser debugging endpoint. */
export async function httpJson(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) throw new Error(`http_${response.status}:${url}`);
  return response.json();
}

/** Reserves an ephemeral local TCP port for a new isolated browser process. */
export async function findFreePort() {
  return new Promise((resolvePromise, rejectPromise) => {
    const server = createServer();
    server.once("error", rejectPromise);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        rejectPromise(new Error("browser_port_unavailable"));
        return;
      }
      server.close((error) => (error ? rejectPromise(error) : resolvePromise(address.port)));
    });
  });
}

function childExited(child) {
  return Boolean(child && (child.exitCode !== null || child.signalCode !== null));
}

async function waitForChildExit(child, timeoutMs, report) {
  if (!child || childExited(child)) return;
  const exitPromise = new Promise((resolvePromise) => child.once("exit", resolvePromise));
  try {
    await withTimeout(exitPromise, timeoutMs, "browser_exit");
  } catch {
    report("browser_exit_timeout");
    child.kill("SIGKILL");
    try {
      await withTimeout(exitPromise, Math.min(timeoutMs, 5_000), "browser_exit_after_escalation");
    } catch {
      throw new Error("browser_exit_after_escalation:timeout");
    }
  }
  if (!childExited(child)) throw new Error("browser_exit_not_confirmed");
}

async function removeProfile(profile, report) {
  let lastError;
  for (let attempt = 0; attempt < 10; attempt += 1) {
    try {
      rmSync(profile, { recursive: true, force: true });
      if (!existsSync(profile)) {
        report("browser_profile_cleaned");
        return;
      }
    } catch (error) {
      lastError = error;
    }
    await sleep(200);
  }
  const error = lastError || new Error("browser_profile_still_exists");
  report("browser_profile_cleanup_failed", { error: String(error) });
  throw error;
}

function trackChild(child, report) {
  for (const [stream, event] of [[child.stdout, "browser_stdout"], [child.stderr, "browser_stderr"]]) {
    stream?.on("data", (chunk) => report(event, { output: String(chunk) }));
  }
  child.once("error", (error) => report("browser_error", { error: String(error) }));
  child.once("exit", (code, signal) => report("browser_exit", { code, signal }));
}

async function waitForHttpReady(url, timeoutMs, label) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      return await httpJson(url);
    } catch {
      await sleep(150);
    }
  }
  throw new Error(`timeout_waiting_for:${label}`);
}

/** Minimal scenario-neutral client for Chrome DevTools Protocol page operations. */
export class CdpClient {
  constructor(webSocketUrl, { timeoutMs = defaultTimeoutMs } = {}) {
    this.webSocketUrl = webSocketUrl;
    this.timeoutMs = timeoutMs;
    this.nextId = 1;
    this.pending = new Map();
  }

  async connect() {
    this.ws = new WebSocket(this.webSocketUrl);
    await withTimeout(
      new Promise((resolvePromise, rejectPromise) => {
        this.ws.addEventListener("open", resolvePromise, { once: true });
        this.ws.addEventListener("error", rejectPromise, { once: true });
      }),
      this.timeoutMs,
      "cdp_connect",
    );
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
    return withTimeout(
      new Promise((resolvePromise, rejectPromise) => {
        this.pending.set(id, { resolve: resolvePromise, reject: rejectPromise });
        this.ws.send(JSON.stringify({ id, method, params }));
      }),
      this.timeoutMs,
      `cdp_send:${method}`,
    );
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
    while (Date.now() - started < this.timeoutMs) {
      const value = await this.evaluate(expression).catch(() => false);
      if (value) return value;
      await sleep(150);
    }
    throw new Error(`timeout_waiting_for:${label}`);
  }

  close() {
    this.ws?.close();
    for (const pending of this.pending.values()) pending.reject(new Error("cdp_closed"));
    this.pending.clear();
  }
}

/** Captures a safely named screenshot and reports only its basename. */
export async function captureScreenshot(client, outputDir, name, report = () => {}) {
  mkdirSync(outputDir, { recursive: true });
  const safeName = name.replace(/[^a-zA-Z0-9_.-]+/g, "-");
  const filePath = join(outputDir, `${safeName}.png`);
  const result = await client.send("Page.captureScreenshot", { format: "png" });
  writeFileSync(filePath, Buffer.from(result.data, "base64"));
  report("screenshot_written", { screenshot: basename(filePath) });
  return basename(filePath);
}

/** Starts an isolated headless Chrome page and returns idempotent cleanup. */
export async function startBrowser({
  viewport,
  timeoutMs = defaultTimeoutMs,
  chromePath,
  profilePrefix = "ai-platform-browser-smoke-",
  report = () => {},
}) {
  const executable = findChrome(chromePath);
  if (!executable) throw new Error("chrome_not_found");

  const port = await findFreePort();
  const profile = mkdtempSync(join(tmpdir(), profilePrefix));
  const endpoint = `http://127.0.0.1:${port}`;
  const reportRedacted = (event, details = {}) =>
    report(redactedValue(event, [profile]), redactedValue(details, [profile]));
  let child;
  let client;
  let closed = false;
  const close = async () => {
    if (closed) return;
    closed = true;
    client?.close();
    child?.kill();
    await waitForChildExit(child, timeoutMs, reportRedacted);
    await removeProfile(profile, reportRedacted);
  };

  try {
    child = spawn(
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
      { stdio: ["ignore", "pipe", "pipe"], windowsHide: true },
    );
    trackChild(child, reportRedacted);
    await waitForHttpReady(`${endpoint}/json/version`, timeoutMs, "browser_cdp");
    const target = await httpJson(
      `${endpoint}/json/new?${encodeURIComponent("about:blank")}`,
      { method: "PUT" },
    );
    client = new CdpClient(target.webSocketDebuggerUrl, { timeoutMs });
    await client.connect();
    await client.send("Page.enable");
    await client.send("Runtime.enable");
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: viewport.mobile,
    });
    reportRedacted("browser_ready", { port });
    return { client, close };
  } catch (error) {
    try {
      await close();
    } catch (cleanupError) {
      throw new Error(redact(`${String(error)}; ${String(cleanupError)}`, [profile]));
    }
    throw new Error(redact(String(error), [profile]));
  }
}
