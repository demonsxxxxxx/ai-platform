#!/usr/bin/env node
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { captureScreenshot, startBrowser } from "./browser-smoke-harness.mjs";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:4173").replace(/\/+$/, "");
const evidenceDir = resolve(
  process.env.AI_PLATFORM_ROUTE_LAYOUT_SMOKE_DIR || "../../.codex-tmp/route-layout-browser-smoke",
);
const profile = {
  agent_id: "agt_support",
  expected_revision: 1,
  name: "支持专家",
  description: "处理企业支持请求。",
  welcome_message: "你好",
  starter_prompts: ["帮我整理请求"],
  capability_summary: "基于当前授权提供支持。",
  recommended_tasks: ["归纳问题", "形成行动项"],
  supported_input_types: ["text", "file"],
  expected_outputs: ["支持建议"],
  permissions_and_data_access_notice: "仅访问当前用户授权的数据。",
  avatar_ref: "builtin:assistant",
  category: "support",
  published_at: "2026-08-09T00:00:00Z",
};
const profiles = [
  profile,
  {
    ...profile,
    agent_id: "agt_contract",
    expected_revision: 3,
    name: "合同审阅专家",
    description: "识别合同中的风险条款、责任边界与缺失约定。",
    capability_summary: "从法务与业务协作视角整理风险、证据和修改建议。",
    recommended_tasks: ["审阅合同条款", "提取履约风险", "形成修改建议"],
    starter_prompts: ["请审阅这份合同并标记需要确认的条款"],
    category: "research",
  },
  {
    ...profile,
    agent_id: "agt_ops",
    expected_revision: 2,
    name: "运营分析专家",
    description: "归纳运营数据，定位异常并形成可执行的改进清单。",
    capability_summary: "将零散运营信息整理为趋势、原因和下一步行动。",
    recommended_tasks: ["分析运营周报", "定位指标异常", "拆解改进动作"],
    starter_prompts: ["请分析这批运营数据并给出优先级建议"],
    category: "operations",
  },
];

function bootstrapSource() {
  return `(() => {
    localStorage.setItem('language', 'zh');
    localStorage.setItem('i18nextLng', 'zh');
    localStorage.setItem('ai-platform-theme', 'light');
    const originalFetch = window.fetch.bind(window);
    const state = window.__routeLayoutSmoke = { errors: [], requests: [] };
    window.addEventListener('error', (event) => state.errors.push(String(event.message)));
    window.addEventListener('unhandledrejection', (event) => state.errors.push(String(event.reason)));
    const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
    const skill = {
      skill_name: 'qa-file-reviewer', expected_version: '0.1.0', input_modes: ['docx'], requires_file: true,
      description: 'Schema-seeded baseline for QA Word Review.', tags: ['review'], files: ['SKILL.md'], enabled: true,
      file_count: 1, installed_from: 'manual', is_published: true, marketplace_is_active: true,
      created_at: '2026-08-09T00:00:00Z', updated_at: '2026-08-09T00:00:00Z'
    };
    const profiles = ${JSON.stringify(profiles)};
    const profile = profiles[0];
    const adminProfile = {
      ...profile,
      revision: profile.expected_revision,
      status: 'published',
      content_hash: 'a'.repeat(64),
      instructions: '你是一位企业支持专家。先理解任务，再从授权 Skill Set 中自主选择需要的能力。',
      model_id: 'model-enterprise',
      selected_skill: { skill_id: 'qa-file-reviewer', expected_version: '0.1.0' },
      skill_set: [{ skill_id: 'qa-file-reviewer', expected_version: '0.1.0' }],
      mcp_tool_ids: [],
      avatar_asset_id: null,
      visibility: 'tenant',
      allowed_department_ids: [],
      allowed_roles: [],
      allowed_user_ids: [],
      created_at: '2026-08-08T00:00:00Z'
    };
    window.fetch = async (input, init = {}) => {
      const raw = typeof input === 'string' ? input : input.url;
      const url = new URL(raw, location.origin);
      const method = String(init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
      if (url.pathname.startsWith('/api/')) state.requests.push({ path: url.pathname, method });
      if (url.pathname === '/api/ai/auth/me') return json({
        user_id: 'layout-admin', user_name: 'layout-admin', display_name: 'Layout Admin', tenant_id: 'tenant-layout',
        roles: ['admin'], permissions: ['chat:read','chat:write','session:read','session:write','skill:admin','skill:read','skill:write','skill:delete','marketplace:admin','agent_profile:admin'],
        is_admin: true, source: 'cookie_session'
      });
      if (url.pathname === '/api/ai/auth/bootstrap' && method === 'POST') return json({
        status: 'ready', protocol_version: 1
      });
      if (url.pathname === '/api/auth/profile') return json({ metadata: { pinned_model_ids: [] } });
      if (url.pathname === '/api/auth/profile/metadata' && method === 'PUT') return json({ metadata: { pinned_model_ids: [] } });
      if (url.pathname === '/api/auth/oauth/providers') return json({
        providers: [], registration_enabled: false,
        turnstile: { enabled: false, site_key: '', require_on_login: false, require_on_register: false, require_on_password_change: false }
      });
      if (url.pathname === '/api/skills' || url.pathname === '/api/skills/') return json({
        skills: [skill], total: 1, skip: 0, limit: 20, available_tags: ['review'],
        effective_permissions: ['skill:admin','skill:read','skill:write','skill:delete'], effective_permissions_known: true, catalog_read_resolved: true
      });
      if (url.pathname === '/api/ai/admin/skills') return json({ items: [{
        skill_id: 'skill-opaque-42', name: 'qa-file-reviewer', description: skill.description,
        lifecycle_status: 'active', distribution_status: 'active', visible_to_user: true,
        latest_version: '0.1.0', latest_version_status: 'active', current_version: '0.1.0', rollout_percent: 100
      }] });
      if (url.pathname === '/api/admin/capability-distributions/department-directory') return json({ departments: [{
        directory_id: '1', authority_id: '运营QA for 工程', name: '运营QA for 工程', path: '运营QA for 工程', children: [], selectable: true, reason: null
      }] });
      if (url.pathname === '/api/admin/capability-distributions') return json({ capability_distributions: [{
        id: 'dist-1', tenant_id: 'tenant-layout', capability_kind: 'skill', capability_id: 'skill-opaque-42',
        status: 'active', visible_to_user: true, scope_mode: 'allowlist', department_ids: ['运营QA for 工程'], allowed_roles: ['reviewer'], metadata_json: {}
      }] });
      if (url.pathname === '/api/admin/capability-distributions/skill/skill-opaque-42' && method === 'PUT') return json({
        id: 'dist-1', tenant_id: 'tenant-layout', capability_kind: 'skill', capability_id: 'skill-opaque-42',
        status: 'active', visible_to_user: true, scope_mode: 'allowlist', department_ids: ['运营QA for 工程'], allowed_roles: ['reviewer'], metadata_json: {}
      });
      if (url.pathname === '/api/roles/') return json({ roles: [{ id: 'reviewer', name: 'reviewer', description: 'Reviewer', permissions: [], is_system: false }], total: 1, skip: 0, limit: 100 });
      if (url.pathname === '/api/ai/agent-profiles') return json({ agent_profiles: profiles });
      if (url.pathname === '/api/ai/agent-profiles/agt_support') return json(profile);
      if (url.pathname === '/api/ai/admin/agent-profiles') return json({ agent_profiles: [adminProfile] });
      if (url.pathname === '/api/ai/admin/agent-profiles/agt_support/history') return json({ agent_profiles: [adminProfile] });
      if (url.pathname === '/api/mcp/chat-tools') return json({ tools: [], count: 0, unavailable: [] });
      if (url.pathname === '/api/agent/models/available') return json({
        models: [{ id: 'model-enterprise', value: 'model-enterprise', label: 'Enterprise Claude', provider: 'anthropic' }],
        count: 1, enabled_count: 1, default_model_id: 'model-enterprise'
      });
      if (url.pathname === '/api/ai/chat/sessions') return json({ sessions: [], next_cursor: null });
      if (url.pathname === '/api/sessions') return json({ sessions: [], total: 0, skip: 0, limit: 20, has_more: false });
      if (url.pathname.includes('notification')) return json([]);
      if (url.pathname.startsWith('/api/')) {
        state.errors.push('unstubbed_api:' + method + ':' + url.pathname);
        return json({ detail: 'route_layout_smoke_unstubbed_api' }, 501);
      }
      return originalFetch(input, init);
    };
  })()`;
}

const cases = [
  {
    path: "/skills",
    selector: "[data-skills-master-detail]",
    name: "skills",
    scroller: "[data-primary-page-scroller]",
    popover: true,
    requiredSelectors: [
      "[data-skill-catalog-item='skill-opaque-42']",
      "[data-selected-skill-detail-shell]",
      "[data-skill-distribution-status]",
      "[data-skill-distribution-visible]",
      "[data-skill-distribution-save]",
      ".department-selector__trigger",
    ],
    saveRequest: "/api/admin/capability-distributions/skill/skill-opaque-42",
    requiredRequests: [
      "/api/skills/",
      "/api/ai/admin/skills",
      "/api/admin/capability-distributions",
      "/api/admin/capability-distributions/department-directory",
    ],
  },
  {
    path: "/agent-market",
    selector: "[data-agent-market]",
    name: "market",
    scroller: "[data-agent-market]",
    requiredSelectors: [
      "[data-agent-market-search]",
      "[data-agent-market-filter]",
      "[data-agent-market-card]",
    ],
    requiredRequests: ["/api/ai/agent-profiles"],
  },
  {
    path: "/agent-market/agt_support/1",
    selector: "[data-agent-market-detail]",
    name: "market-detail",
    scroller: "[data-agent-market-detail]",
    requiredSelectors: ["[data-agent-market-start-chat]"],
    requiredRequests: ["/api/ai/agent-profiles/agt_support"],
  },
  {
    path: "/agent-market/agt_support/1/chat",
    selector: "[data-agent-workspace-welcome], [data-workbench-region='thread']",
    name: "market-workspace",
    requiredSelectors: ["[data-agent-workspace-welcome]", "[data-agent-workspace-start]"],
    requiredRequests: ["/api/ai/agent-profiles/agt_support"],
  },
  {
    path: "/agent-builder",
    selector: "[data-agent-builder-workbench]",
    name: "builder",
    scroller: "[data-agent-builder-workbench] > div:last-child",
    requiredSelectors: ["[data-agent-builder-workbench] input", "[data-agent-builder-save-reason]"],
    requiredRequests: ["/api/ai/admin/agent-profiles", "/api/skills/"],
  },
];
const viewports = [
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "tablet", width: 768, height: 900, mobile: false },
  { name: "mobile", width: 390, height: 844, mobile: true },
];
const requestedCase = process.env.AI_PLATFORM_ROUTE_LAYOUT_CASE;
const requestedViewport = process.env.AI_PLATFORM_ROUTE_LAYOUT_VIEWPORT;
const selectedCases = requestedCase
  ? cases.filter((scenario) => scenario.name === requestedCase)
  : cases;
const selectedViewports = requestedViewport
  ? viewports.filter((viewport) => viewport.name === requestedViewport)
  : viewports;
if (selectedCases.length === 0 || selectedViewports.length === 0) {
  throw new Error("route_layout_smoke_filter_invalid");
}

async function runCase(viewport, scenario) {
  const browser = await startBrowser({ viewport, profilePrefix: `route-layout-${viewport.name}-` });
  try {
    await browser.client.send("Page.addScriptToEvaluateOnNewDocument", { source: bootstrapSource() });
    await browser.client.send("Page.navigate", { url: `${baseUrl}${scenario.path}` });
    try {
      await browser.client.waitFor(
        `Boolean(document.querySelector(${JSON.stringify(scenario.selector)}))`,
        `${viewport.name}:${scenario.name}`,
      );
    } catch (error) {
      const diagnostic = await browser.client.evaluate(`(() => ({
        url: location.href,
        text: document.body?.innerText?.slice(0, 1000) || '',
        errors: window.__routeLayoutSmoke?.errors || [],
        requests: window.__routeLayoutSmoke?.requests || []
      }))()`);
      throw new Error(`route_mount_failed:${viewport.name}:${scenario.name}:${JSON.stringify(diagnostic)}:${String(error)}`);
    }
    await browser.client.waitFor(
      `(${JSON.stringify(scenario.requiredSelectors ?? [])}).every((selector) => Boolean(document.querySelector(selector)))`,
      `${viewport.name}:${scenario.name}:required-controls`,
    );
    const layout = await browser.client.evaluate(`(() => {
      const target = ${scenario.scroller ? `document.querySelector(${JSON.stringify(scenario.scroller)})` : "document.scrollingElement"};
      if (target) target.scrollTop = target.scrollHeight;
      const rect = target?.getBoundingClientRect();
      const requestedPaths = new Set(window.__routeLayoutSmoke.requests.map((request) => request.path));
      return {
        bodyScrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        scrollTop: target?.scrollTop || 0,
        scrollHeight: target?.scrollHeight || 0,
        clientHeight: target?.clientHeight || 0,
        targetRect: rect ? { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom } : null,
        errors: window.__routeLayoutSmoke.errors,
        requests: [...requestedPaths],
      };
    })()`);
    const missingRequests = (scenario.requiredRequests ?? []).filter(
      (path) => !layout.requests.includes(path),
    );
    const reachable = layout.scrollHeight <= layout.clientHeight + 1 || layout.scrollTop + layout.clientHeight >= layout.scrollHeight - 2;
    if (
      layout.bodyScrollWidth > layout.viewportWidth ||
      !reachable ||
      layout.errors.length ||
      missingRequests.length
    ) {
      throw new Error(`layout_failed:${viewport.name}:${scenario.name}:${JSON.stringify({ layout, reachable, missingRequests })}`);
    }
    let overlay = null;
    if (scenario.popover) {
      await browser.client.evaluate(`(() => { const node = document.querySelector('.department-selector__trigger'); if (!node) throw new Error('department_trigger_missing'); node.click(); })()`);
      await browser.client.waitFor("Boolean(document.querySelector('.department-selector__menu'))", `${viewport.name}:department-menu`);
      overlay = await browser.client.evaluate(`(() => { const r = document.querySelector('.department-selector__menu').getBoundingClientRect(); return { left:r.left, right:r.right, top:r.top, bottom:r.bottom, visible:r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight }; })()`);
      if (!overlay.visible) throw new Error(`overlay_clipped:${viewport.name}:${JSON.stringify(overlay)}`);
    }
    if (scenario.saveRequest) {
      await browser.client.evaluate(`(() => { const button = document.querySelector('[data-skill-distribution-save]'); if (!button) throw new Error('skill_distribution_save_missing'); button.click(); })()`);
      await browser.client.waitFor(
        `window.__routeLayoutSmoke.requests.some((request) => request.path === ${JSON.stringify(scenario.saveRequest)} && request.method === 'PUT')`,
        `${viewport.name}:skill-distribution-save`,
      );
    }
    const screenshot = await captureScreenshot(browser.client, evidenceDir, `${viewport.name}-${scenario.name}`);
    return { viewport: viewport.name, route: scenario.path, layout, reachable, overlay, screenshot };
  } finally {
    await browser.close();
  }
}

const results = [];
for (const viewport of selectedViewports) {
  for (const scenario of selectedCases) results.push(await runCase(viewport, scenario));
}
const evidence = { schema: "ai-platform.route-layout-browser-smoke.v1", mock_backed: true, baseUrl, status: "passed", results };
mkdirSync(evidenceDir, { recursive: true });
writeFileSync(resolve(evidenceDir, "evidence.json"), `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify(evidence));
