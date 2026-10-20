#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { captureScreenshot, startBrowser } from "./browser-smoke-harness.mjs";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:4173").replace(/\/+$/, "");
const evidenceDir = resolve(process.env.AI_PLATFORM_ROUTE_LAYOUT_SMOKE_DIR || "../../.codex-tmp/chat-performance");
const viewport = { name: "desktop", width: 1440, height: 900, mobile: false };
const iterations = 3;
const bootstrapSource = `(() => {
  window.__performanceErrors = [];
  window.addEventListener('error', event => window.__performanceErrors.push(event.message));
  window.addEventListener('unhandledrejection', event => window.__performanceErrors.push(String(event.reason)));
  localStorage.setItem('language', 'zh');
  localStorage.setItem('i18nextLng', 'zh');
  localStorage.setItem('ai-platform-theme', 'light');
  const json = value => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } });
  const profile = { agent_id: 'agt_fixture', expected_revision: 1, name: '性能合成专家', description: '固定性能样本。', starter_prompts: [], avatar_ref: 'builtin:assistant', avatar_seed: 'perf', market_tags: [], completed_tasks: 0, is_favorite: false, published_at: '2026-01-01T00:00:00Z' };
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' ? input : input.url, location.origin);
    if (url.pathname === '/api/ai/auth/me') return json({ user_id: 'performance-user', user_name: 'performance-user', display_name: 'Performance', tenant_id: 'performance-tenant', roles: [], permissions: ['chat:read', 'chat:write'], is_admin: false, source: 'synthetic' });
    if (url.pathname === '/api/ai/auth/bootstrap') return json({ status: 'ready', protocol_version: 2, generation: 1 });
    if (url.pathname === '/api/ai/agent-profiles') return json({ agent_profiles: [profile] });
    if (url.pathname === '/api/ai/agent-profiles/agt_fixture') return json(profile);
    if (url.pathname === '/api/upload/config') return json({ enabled: false, provider: 'none', maxFiles: 0, uploadLimitsBytes: {} });
    if (url.pathname.startsWith('/api/')) return json({});
    return originalFetch(input, init);
  };
})()`;

async function runIteration(label, index) {
  const browser = await startBrowser({ viewport, profilePrefix: `chat-performance-${label}-${index}-` });
  try {
    await browser.client.send("Page.addScriptToEvaluateOnNewDocument", { source: bootstrapSource });
    const started = performance.now();
    await browser.client.send("Page.navigate", { url: `${baseUrl}/scripts/fixtures/chat-integration.html?performance=1` });
    await browser.client.waitFor("Boolean(document.querySelector('[data-performance-ready]')) && Boolean(document.querySelector('[data-message-id=performance-assistant-499]')) && document.querySelectorAll('[data-chat-activity-status]').length === 50", `${label}:mount`);
    const navigationToReadyMs = performance.now() - started;
    const inputToPaintMs = await browser.client.evaluate(`(async () => {
      const input = document.querySelector('textarea');
      const startedAt = performance.now();
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '固定输入事件');
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await new Promise(requestAnimationFrame);
      await new Promise(requestAnimationFrame);
      if (input.value !== '固定输入事件') throw new Error('input_not_retained');
      return performance.now() - startedAt;
    })()`);
    await browser.client.evaluate(`(() => {
      const rows = [...document.querySelectorAll('[data-message-id]')];
      window.__performanceRows = rows.filter(row => row.dataset.messageId !== 'performance-assistant-499');
    })()`);
    const stream20Ms = await browser.client.evaluate("window.__chatIntegration.performanceStream(20)");
    await browser.client.waitFor(`document.querySelector('[data-message-id=performance-assistant-499] .markdown-preview')?.textContent === '固定流式公开更新。'.repeat(20)`, `${label}:stream-final`);
    const retainedRows = await browser.client.evaluate(`({ before: window.__performanceRows.length, retained: window.__performanceRows.filter(row => row.isConnected).length })`);
    const scroll = await browser.client.evaluate(`(async () => {
      const view = document.querySelector('[data-virtuoso-scroller]');
      const startedAt = performance.now();
      view.scrollTop = view.scrollHeight;
      for (let frame = 0; frame < 4; frame++) await new Promise(requestAnimationFrame);
      return { ms: performance.now() - startedAt, scrollTop: view.scrollTop, scrollHeight: view.scrollHeight, clientHeight: view.clientHeight, reachable: view.scrollTop + view.clientHeight >= view.scrollHeight - 2 };
    })()`);
    const dom = await browser.client.evaluate(`({ messageCount: document.querySelectorAll('[data-message-id]').length, publicToolCount: document.querySelectorAll('[data-chat-activity-status]').length, mountedMarkMs: performance.getEntriesByName('chat-fixture-mounted')[0]?.startTime ?? null })`);
    assert.ok(dom.messageCount > 0 && dom.messageCount < 100, JSON.stringify(dom));
    assert.equal(dom.publicToolCount, 50);
    assert.ok(scroll.reachable, JSON.stringify(scroll));
    assert.deepEqual(await browser.client.evaluate('window.__performanceErrors'), []);
    const screenshot = await captureScreenshot(browser.client, evidenceDir, `${label}-${index}`);
    return { index, navigationToReadyMs, inputToPaintMs, stream20Ms, scroll, dom, retainedRows, screenshot };
  } finally {
    await browser.close();
  }
}

const warmup = await runIteration("warmup", 0);
const samples = [];
for (let index = 1; index <= iterations; index += 1) samples.push(await runIteration("measured", index));
const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
const summary = {
  schema: "ai-platform.chat-performance.v1",
  synthetic: true,
  baseUrl,
  viewport,
  fixedData: { messages: 1000, turns: 500, longPublicTools: 50, streamUpdates: 20 },
  warmup,
  samples,
  median: {
    navigationToReadyMs: median(samples.map(sample => sample.navigationToReadyMs)),
    inputToPaintMs: median(samples.map(sample => sample.inputToPaintMs)),
    stream20Ms: median(samples.map(sample => sample.stream20Ms)),
    scrollMs: median(samples.map(sample => sample.scroll.ms)),
  },
};
mkdirSync(evidenceDir, { recursive: true });
writeFileSync(resolve(evidenceDir, "performance.json"), `${JSON.stringify(summary, null, 2)}\n`);
console.log(JSON.stringify(summary));
