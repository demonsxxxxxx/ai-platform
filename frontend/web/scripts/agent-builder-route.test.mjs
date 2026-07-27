#!/usr/bin/env node
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { captureScreenshot, startBrowser } from "./browser-smoke-harness.mjs";

const baseUrl = (process.argv[2] || "http://127.0.0.1:3001").replace(/\/+$/, "");
const evidenceDir = resolve("../../.codex-tmp/agent-builder-route-test");

function bootstrapSource() {
  return `(() => {
    const key = '__agentBuilderRouteTest';
    const requestedScenario = new URLSearchParams(location.search).get('scenario') || 'builder';
    const load = () => JSON.parse(sessionStorage.getItem(key) || JSON.stringify({ calls: [], errors: [], phase: 'initial', scenario: requestedScenario }));
    const save = (state) => sessionStorage.setItem(key, JSON.stringify(state));
    const recordError = (value) => { const state = load(); state.errors.push(String(value)); save(state); };
    window.addEventListener('error', (event) => recordError(event.message));
    window.addEventListener('unhandledrejection', (event) => recordError(event.reason));
    const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
    const originalFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
      const rawUrl = typeof input === 'string' ? input : input.url;
      const url = new URL(rawUrl, location.origin);
      const method = String(init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
      let body = null;
      if (typeof init.body === 'string') { try { body = JSON.parse(init.body); } catch { body = '[unparsed]'; } }
      const state = load();
      if (url.pathname.startsWith('/api/')) { state.calls.push({ path: url.pathname, method, body }); save(state); }
      const refreshed = state.phase === 'refreshed';
      if (url.pathname === '/api/ai/auth/me') {
        if (state.scenario === 'unauth') return json({ detail: 'not authenticated' }, 401);
        return json({ user_id: 'ordinary-user', user_name: 'ordinary-user', display_name: 'Ordinary user', tenant_id: 'tenant-route', roles: ['member'], permissions: ['chat:write', 'skill:read'], is_admin: false, source: 'cookie_session' });
      }
      if (url.pathname === '/api/auth/profile') return json({ metadata: { pinned_model_ids: [] } });
      if (url.pathname === '/api/auth/oauth/providers') return json({ providers: [], registration_enabled: false, turnstile: { enabled: false, site_key: '', require_on_login: false, require_on_register: false, require_on_password_change: false } });
      if (url.pathname === '/api/skills' || url.pathname === '/api/skills/') return json({ skills: [{ skill_name: 'document-review', expected_version: refreshed ? '2026.07.28' : '2026.07.27', input_modes: [], requires_file: false, description: 'Review an authorized document.', tags: [], files: [], enabled: true, file_count: 0, installed_from: 'manual', is_published: false, marketplace_is_active: false }, { skill_name: 'file-review', expected_version: '2026.07.27', input_modes: [], requires_file: true, description: 'Requires a file.', tags: [], files: [], enabled: true, file_count: 0, installed_from: 'manual', is_published: false, marketplace_is_active: false }], total: 2, skip: 0, limit: 50, available_tags: [], effective_permissions: ['skill:read'], effective_permissions_known: true, catalog_read_resolved: true });
      if (url.pathname === '/api/mcp/chat-tools') return json({ tools: [{ tool_id: refreshed ? 'mcp:knowledge:search:v2' : 'mcp:knowledge:search', label: refreshed ? 'Knowledge search v2' : 'Knowledge search', description: 'Search approved knowledge.', category: 'mcp' }] });
      if (url.pathname === '/api/agent/models/available') return json({ models: [{ id: 'route-model', value: refreshed ? 'route/model-v2' : 'route/model', label: refreshed ? 'Route model v2' : 'Route model' }], count: 1, enabled_count: 1, default_model_id: 'route-model' });
      if (url.pathname === '/api/chat/stream') return json({ session_id: 'session-route', run_id: 'run-route', trace_id: 'trace-route', status: 'queued', submission_id: body?.submission_id });
      if (url.pathname === '/api/sessions') return json({ sessions: [], total: 0, skip: 0, limit: 50, has_more: false });
      if (url.pathname === '/api/sessions/session-route') return json({ id: 'session-route', agent_id: 'general-agent', created_at: '2026-07-27T00:00:00.000Z', updated_at: '2026-07-27T00:00:00.000Z', is_active: true, metadata: { current_run_id: 'run-route' } });
      if (url.pathname === '/api/sessions/session-route/events') return json({ events: [], run_id: 'run-route' });
      if (url.pathname === '/api/sessions/session-route/runs') return json({ session_id: 'session-route', runs: [{ run_id: 'run-route', status: 'queued', trace_id: 'trace-route' }], count: 1 });
      if (url.pathname.includes('/generate-title')) return json({ title: 'Route session', session_id: 'session-route' });
      if (url.pathname.startsWith('/api/chat/sessions/session-route/stream')) return new Response('', { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
      if (url.pathname.startsWith('/api/')) return json({});
      return originalFetch(input, init);
    };
  })()`;
}

function clickByText(text) {
  return `(() => { const node = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes(${JSON.stringify(text)})); if (!node) throw new Error('button_not_found:${text}'); node.focus(); node.click(); return true; })()`;
}

function clickSelector(selector) {
  return `(() => { const node = document.querySelector(${JSON.stringify(selector)}); if (!node) throw new Error('control_not_found:${selector}'); node.focus(); node.click(); return true; })()`;
}

function clickDialogButton(text) {
  return `(() => { const dialog = document.querySelector('[role=dialog]'); const node = Array.from(dialog?.querySelectorAll('button') || []).find((button) => button.textContent?.includes(${JSON.stringify(text)})); if (!node) throw new Error('dialog_button_not_found:${text}'); node.focus(); node.click(); return true; })()`;
}

function setControl(selector, value) {
  return `(() => { const control = document.querySelector(${JSON.stringify(selector)}); if (!control) throw new Error('control_not_found:${selector}'); const prototype = control instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLTextAreaElement.prototype; Object.getOwnPropertyDescriptor(prototype, 'value').set.call(control, ${JSON.stringify(value)}); control.dispatchEvent(new Event('input', { bubbles: true })); control.dispatchEvent(new Event('change', { bubbles: true })); return true; })()`;
}

async function chooseSkill(browser, name, skillName, currentButtonText) {
  await browser.client.evaluate(clickByText(currentButtonText));
  await browser.client.waitFor("Boolean(document.querySelector('[role=dialog]'))", `${name}_${skillName}_dialog`);
  await browser.client.evaluate(clickDialogButton(skillName));
}

async function chooseOnlyMcp(browser, name) {
  await browser.client.evaluate(clickSelector('button[aria-label="Configure MCP tools"]'));
  await browser.client.waitFor("Boolean(document.querySelector('[role=dialog]'))", `${name}_mcp_dialog`);
  await browser.client.evaluate(clickSelector('[role=dialog] input[type=checkbox]'));
  await browser.client.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");
}

async function runAuthGuard(name, viewport) {
  const browser = await startBrowser({ viewport, profilePrefix: `agent-builder-auth-${name}-` });
  try {
    await browser.client.send('Page.addScriptToEvaluateOnNewDocument', { source: bootstrapSource() });
    await browser.client.send('Page.navigate', { url: `${baseUrl}/agent-builder?scenario=unauth` });
    await browser.client.waitFor("location.pathname === '/auth/login'", `${name}_auth_redirect`);
    await browser.client.waitFor("Boolean(document.querySelector('form button[type=submit]'))", `${name}_login_form`);
    const screenshot = await captureScreenshot(browser.client, evidenceDir, `${name}-agent-builder-auth-guard`);
    const hasErrorBoundary = await browser.client.evaluate("document.body.innerText.includes('Cannot read properties of undefined')");
    if (hasErrorBoundary) throw new Error(`${name}_auth_guard_error_boundary`);
    return { name, pathname: '/auth/login', screenshot, loginFormVisible: true };
  } finally {
    await browser.close();
  }
}

async function runBuilderScenario(name, viewport) {
  const browser = await startBrowser({ viewport, profilePrefix: `agent-builder-route-${name}-` });
  try {
    await browser.client.send('Page.addScriptToEvaluateOnNewDocument', { source: bootstrapSource() });
    await browser.client.send('Page.navigate', { url: `${baseUrl}/agent-builder?scenario=builder` });
    await browser.client.waitFor("Boolean(document.querySelector('[data-agent-builder-workbench]'))", `${name}_builder_route`);
    await browser.client.waitFor("document.querySelector('select option[value=route-model]')?.textContent?.includes('Route model') === true", `${name}_catalog_ready`);

    await browser.client.evaluate(setControl('textarea[aria-label="Preview message"]', 'Review this authorized document'));
    await browser.client.evaluate(clickByText('Edit'));
    await browser.client.waitFor("Boolean(document.querySelector('[role=dialog]'))", `${name}_instructions_dialog`);
    await browser.client.evaluate(setControl('[role=dialog] textarea', 'LOCAL-ONLY-INSTRUCTION-SENTINEL'));
    await browser.client.evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))");

    await chooseSkill(browser, name, 'file-review', 'Select Skill');
    await browser.client.waitFor("document.body.innerText.includes('File upload is unavailable in Agent Builder')", `${name}_file_required_warning`);
    const fileRequiredBlocked = await browser.client.evaluate(`(() => {
      const submit = Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.includes('Open Chat run'));
      const state = JSON.parse(sessionStorage.getItem('__agentBuilderRouteTest') || '{"calls":[]}');
      return submit?.disabled === true && state.calls.filter((call) => call.path === '/api/chat/stream').length === 0;
    })()`);

    await chooseSkill(browser, name, 'document-review', 'file-review');
    await chooseOnlyMcp(browser, `${name}_initial`);
    await browser.client.evaluate(setControl('select', 'route-model'));

    await browser.client.evaluate(`(() => { const state = JSON.parse(sessionStorage.getItem('__agentBuilderRouteTest')); state.phase = 'refreshed'; sessionStorage.setItem('__agentBuilderRouteTest', JSON.stringify(state)); })()`);
    await browser.client.evaluate(clickSelector('button[aria-label="Refresh catalogs"]'));
    await browser.client.waitFor("document.querySelector('select option[value=route-model]')?.textContent?.includes('Route model v2') === true && document.querySelector('button[aria-label=\"Refresh catalogs\"]')?.disabled === false", `${name}_catalog_refreshed`);

    const staleIdentitiesPreserved = await browser.client.evaluate("document.body.innerText.includes('document-review') && document.body.innerText.includes('MCP tools\\n1') && document.querySelector('select')?.value === ''");
    await browser.client.evaluate(clickByText('Open Chat run'));
    await browser.client.waitFor("document.body.innerText.includes('selected Skill changed')", `${name}_stale_submit_blocked`);
    const stalePostCount = await browser.client.evaluate(`JSON.parse(sessionStorage.getItem('__agentBuilderRouteTest')).calls.filter((call) => call.path === '/api/chat/stream').length`);

    await chooseSkill(browser, name, 'document-review', 'document-review');
    await chooseOnlyMcp(browser, `${name}_current`);
    await browser.client.evaluate(setControl('select', 'route-model'));
    const overflowBeforeSubmit = await browser.client.evaluate('document.documentElement.scrollWidth <= window.innerWidth');
    const screenshot = await captureScreenshot(browser.client, evidenceDir, `${name}-agent-builder-current-catalogs`);
    await browser.client.evaluate(clickByText('Open Chat run'));
    try {
      await browser.client.waitFor("location.pathname === '/chat/session-route'", `${name}_chat_handoff`);
    } catch (error) {
      const debug = await browser.client.evaluate(`(() => ({
        pathname: location.pathname,
        text: document.body.innerText,
        state: JSON.parse(sessionStorage.getItem('__agentBuilderRouteTest') || '{"calls":[],"errors":[]}'),
      }))()`);
      throw new Error(`${error.message}:${JSON.stringify(debug)}`);
    }

    const verification = await browser.client.evaluate(`(() => {
      const state = JSON.parse(sessionStorage.getItem('__agentBuilderRouteTest') || '{"calls":[],"errors":[]}');
      const submits = state.calls.filter((call) => call.path === '/api/chat/stream');
      return { pathname: location.pathname, submits, errors: state.errors, fakeAssistantVisible: document.body.innerText.includes('Synthetic assistant response') };
    })()`);
    const body = verification.submits[0]?.body;
    const bodyText = JSON.stringify(body);
    if (
      !fileRequiredBlocked ||
      !staleIdentitiesPreserved ||
      stalePostCount !== 0 ||
      !overflowBeforeSubmit ||
      verification.pathname !== '/chat/session-route' ||
      verification.submits.length !== 1 ||
      body?.message !== 'Review this authorized document' ||
      body?.agent_options?.model !== 'route/model-v2' ||
      body?.selected_skill?.skill_id !== 'document-review' ||
      body?.selected_skill?.expected_version !== '2026.07.28' ||
      JSON.stringify(body?.selected_mcp_tool_ids) !== JSON.stringify(['mcp:knowledge:search:v2']) ||
      bodyText.includes('LOCAL-ONLY-INSTRUCTION-SENTINEL') ||
      verification.fakeAssistantVisible ||
      verification.errors.length > 0
    ) {
      throw new Error(`agent_builder_route_verification_failed:${JSON.stringify({ fileRequiredBlocked, staleIdentitiesPreserved, stalePostCount, overflowBeforeSubmit, verification })}`);
    }
    return { name, screenshot, fileRequiredBlocked, staleIdentitiesPreserved, stalePostCount, verification };
  } finally {
    await browser.close();
  }
}

const desktop = { width: 1440, height: 900, mobile: false };
const mobile = { width: 390, height: 844, mobile: true };
const [desktopAuth, mobileAuth, desktopBuilder, mobileBuilder] = await Promise.all([
  runAuthGuard('desktop', desktop),
  runAuthGuard('mobile', mobile),
  runBuilderScenario('desktop', desktop),
  runBuilderScenario('mobile', mobile),
]);
const evidence = {
  baseUrl,
  status: 'passed',
  synthetic_identities_only: true,
  authGuards: [desktopAuth, mobileAuth],
  builders: [desktopBuilder, mobileBuilder],
};
mkdirSync(evidenceDir, { recursive: true });
writeFileSync(resolve(evidenceDir, 'evidence.json'), `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify(evidence));
