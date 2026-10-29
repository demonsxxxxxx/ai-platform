#!/usr/bin/env node
import assert from "node:assert/strict";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { captureScreenshot, startBrowser } from "./browser-smoke-harness.mjs";
import { checkChatIntegration } from "./chat-integration-browser-checks.mjs";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:4173").replace(/\/+$/, "");
const theme = process.env.AI_PLATFORM_ROUTE_LAYOUT_THEME || "light";
const directorySize = Number(process.env.AI_PLATFORM_ROUTE_LAYOUT_DIRECTORY_SIZE || 205);
if (!Number.isInteger(directorySize) || directorySize < 205 || directorySize > 50000) throw new Error("route_layout_directory_size_invalid");
if (!["light", "dark"].includes(theme)) throw new Error("route_layout_smoke_theme_invalid");
const evidenceDir = resolve(
  process.env.AI_PLATFORM_ROUTE_LAYOUT_SMOKE_DIR || "../../.codex-tmp/route-layout-browser-smoke",
);
const profile = {
  agent_id: "agt_support",
  expected_revision: 1,
  name: "支持专家",
  description: "处理企业支持请求。",
  starter_prompts: ["帮我整理请求"],
  avatar_ref: "builtin:assistant",
  avatar_seed: "support-expert-1",
  market_tags: ["support"],
  completed_tasks: 0,
  is_favorite: false,
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
    starter_prompts: ["请审阅这份合同并标记需要确认的条款"],
    avatar_ref: "builtin:research",
    avatar_seed: "contract-expert-3",
    market_tags: ["research"],
  },
  {
    ...profile,
    agent_id: "agt_ops",
    expected_revision: 2,
    name: "运营分析专家",
    description: "归纳运营数据，定位异常并形成可执行的改进清单。",
    starter_prompts: ["请分析这批运营数据并给出优先级建议"],
    avatar_ref: "builtin:document",
    avatar_seed: "operations-expert-2",
    market_tags: ["operations"],
  },
];

function bootstrapSource(loading = false, chatFiles = false) {
  return `(() => {
    localStorage.setItem('language', 'zh');
    localStorage.setItem('i18nextLng', 'zh');
    localStorage.setItem('ai-platform-theme', ${JSON.stringify(theme)});
    localStorage.setItem('ai-platform-sidebar-collapsed', 'false');
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
    const run = {
      run_id: 'run-failed', session_id: 'session-synthetic', session_title: '采购合同审核（合成案例）',
      task_summary: '检查超时异常与后续处理的观察关联', user_id: 'user-synthetic', user_display_name: '合成测试用户',
      workspace_id: 'workspace-synthetic', workspace_name: '测试工作区', trace_id: 'trace-synthetic',
      status: 'failed', agent_id: 'agent-synthetic', agent_name: '合同审阅专家', skill_name: '合同检查',
      created_at: '2026-04-01T09:00:00Z', queued_at: '2026-04-01T09:00:00Z',
      started_at: '2026-04-01T09:00:01Z', finished_at: '2026-04-01T09:00:05Z',
      error_code: 'executor_failed', error_message: '任务执行失败'
    };
    const monitorWindow = { from_at: '2026-04-01T00:00:00Z', to_at: '2026-04-02T00:00:00Z' };
    const metric = (value) => ({ sample_count: value === null ? 0 : 1, p50: value, p95: value, p99: value });
    const rootId = 'obs_' + '1'.repeat(32), wrapperId = 'obs_' + '2'.repeat(32), receiptId = 'obs_' + '3'.repeat(32);
    const diagnostics = {
      schema_version: 'ai-platform.run-diagnostics.v1', diagnostic_id: 'rdiag-synthetic', revision: 1,
      coverage: 'full', run,
      root: { observation_id: rootId, attempt_id: 'attempt-a', kind: 'failure', source: 'sdk_exception',
        stage: 'model_wait', error_code: 'provider_timeout', exception_type: 'TimeoutError', message: '模型等待超时；已保留原始异常摘要。' },
      handling: [{ observation_id: wrapperId, parent_observation_id: rootId, attempt_id: 'attempt-a',
        kind: 'handling', source: 'executor_wrapper', stage: 'sandbox_submission', error_code: 'executor_failed' }],
      losses: [], attempts: [{ attempt_id: 'attempt-a', ordinal: 1, status: 'failed', owner_kind: 'queue_worker', terminal_reason: 'run_failed' }],
      details: { schema_version: 'ai-platform.sdk-runtime-diagnostics.v1', sdk: {},
        observations: [{ observation_id: receiptId, parent_observation_id: wrapperId, attempt_id: 'attempt-a',
          source: 'executor_callback', stage: 'terminal_receipt', error_code: 'executor_failed', sdk: {},
          tool_lifecycles: [], tool_calls: [], tool_policy_denials: [], normalization_losses: [] }],
        tool_lifecycles: [], tool_calls: [], tool_policy_denials: [] },
      versions: {}, counts: { retained_observations: 1, omitted_observations: 0 }
    };
    const profile = profiles[0];
    const adminProfile = {
      ...profile,
      revision: profile.expected_revision,
      published_revision: profile.expected_revision,
      status: 'published',
      content_hash: 'a'.repeat(64),
      instructions: '你是一位企业支持专家。先理解任务，再从授权 Skill Set 中自主选择需要的能力。',
      skill_set: [{ skill_id: 'qa-file-reviewer' }],
      mcp_tool_ids: [],
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
      if (${loading} && url.pathname === '/api/ai/auth/me') return new Promise(() => {});
      if (url.pathname === '/api/ai/auth/me') return json({
        user_id: 'layout-admin', user_name: 'layout-admin', display_name: 'Layout Admin', tenant_id: 'tenant-layout',
        roles: ['admin'], permissions: ['chat:read','chat:write','session:read','session:write','skill:admin','skill:read','skill:write','skill:delete','agent_profile:admin','model:admin'],
        is_admin: true, source: 'cookie_session'
      });
      if (url.pathname === '/api/ai/auth/bootstrap' && method === 'POST') return json({
        status: 'ready', protocol_version: 2, generation: 1
      });
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
        latest_version: '0.1.0', latest_version_status: 'active', current_version: '0.1.0',
        latest_display_version: '0.1.0', current_display_version: '0.1.0', latest_uploaded_at: '2026-08-09T00:00:00Z', rollout_percent: 100
      }] });
      if (url.pathname === '/api/admin/capability-distributions/department-directory' && ${groupCase === "members"}) return json({ departments: [{ directory_id: '123', authority_id: '123', name: '总公司', path: '总公司', selectable: true, reason: null, children: [{ directory_id: '456', authority_id: '456', name: '销售与客户支持事业部', path: '总公司 / 销售与客户支持事业部', children: [], selectable: true, reason: null }] }] });
      if (url.pathname === '/api/admin/capability-distributions/department-directory') return json({ departments: [{
        directory_id: '1', authority_id: '1', name: '运营QA for 工程', path: '运营QA for 工程', children: [], selectable: true, reason: null
      }] });
      if (url.pathname === '/api/admin/capability-distributions') return json({ capability_distributions: [{
        id: 'dist-1', tenant_id: 'tenant-layout', capability_kind: 'skill', capability_id: 'skill-opaque-42',
        status: 'active', visible_to_user: true, scope_mode: 'allowlist', department_ids: ['1'], allowed_roles: ['reviewer'], metadata_json: {}
      }] });
      if (url.pathname === '/api/admin/capability-distributions/skill/skill-opaque-42' && method === 'PUT') return json({ capability_distribution: {
        id: 'dist-1', tenant_id: 'tenant-layout', capability_kind: 'skill', capability_id: 'skill-opaque-42',
        status: 'active', visible_to_user: true, scope_mode: 'allowlist', department_ids: ['1'], allowed_roles: ['reviewer'], metadata_json: {}
      } });
      if (url.pathname === '/api/roles/') return json({ roles: [{ id: 'reviewer', name: 'reviewer', description: 'Reviewer', permissions: [], is_system: false }], total: 1, skip: 0, limit: 100 });
      if (url.pathname === '/api/ai/agent-profiles') return json({ agent_profiles: profiles });
      if (url.pathname === '/api/ai/agent-profiles/agt_support') return json(profile);
      if (url.pathname === '/api/ai/admin/agent-profiles') return json({ agent_profiles: [adminProfile] });
      if (url.pathname === '/api/ai/admin/agent-profiles/agt_support/history') return json({ agent_profiles: [adminProfile] });
      if (url.pathname === '/api/mcp/catalog-tools') return json({ tools: [
        { tool_id: 'mcp:drive:upload_file', label: 'ProfileDriveMCPServer.upload_file', description: 'Upload authorized attachments.', server: 'drive', category: 'mcp', cached: false },
        { tool_id: 'mcp:support:search', label: '支持知识检索', description: '检索授权支持知识。', server: 'support', category: 'mcp', cached: false },
        { tool_id: 'mcp:calendar:list', label: '列出会议', description: '查看授权日历。', server: 'calendar', category: 'mcp', cached: false }
      ], count: 3, unavailable: [] });
      if (url.pathname === '/api/mcp/chat-tools') return json({ tools: [], count: 0, unavailable: [] });
      if (url.pathname === '/api/mcp/') return json({ servers: [{
        name: 'synthetic-mcp', transport: 'streamable_http', url: 'https://example.invalid/mcp',
        enabled: true, is_system: true, can_edit: true, allowed_roles: [], role_quotas: {}, credential_state: 'configured'
      }], total: 1, skip: 0, limit: 20 });
      if (url.pathname === '/api/mcp/synthetic-mcp/catalog') return json({
        server_name: 'synthetic-mcp', tools: [{ name: 'synthetic-search', description: 'Synthetic search tool', parameters: [] }], count: 1, unavailable_reason: null
      });
      if (url.pathname === '/api/ai/admin/runs/monitor') return json({
        measurement: 'run_lifecycle.v1', window: monitorWindow, counts: { total: 1, succeeded: 0, failed: 1, cancelled: 0, queued: 0, running: 0 },
        success_rate: 0, latency: { total_ms: metric(5000), queue_ms: metric(1000), execution_ms: metric(4000), server_first_text_ms: metric(null), browser_first_text_ms: metric(null) },
        models: [], runs: [run], limit: 50, has_more: false, order: 'slow'
      });
      if (url.pathname === '/api/ai/admin/runs/run-failed') return json({
        run, worker_execution: { response: '', messages: [], actions: [], model: { turn_count: 1, duration_ms: 4000, stop_category: 'failed' } },
        events: [], steps: [], artifacts: [], sandbox_leases: [], skill_snapshots: [], audit: []
      });
      if (url.pathname === '/api/ai/admin/runs/run-failed/diagnostics') return json(diagnostics);
      if (url.pathname === '/api/ai/admin/runs/run-failed/investigation') return json({
        run_id: run.run_id, total_ms: 5000, queue_ms: 1000, execution_ms: 4000, elapsed_ms: 5000,
        server_first_text_ms: null, browser_first_text_ms: null, first_public_text_at: null,
        observed_at: run.finished_at, browser_measurement: null, timeline_source: 'platform_receipts', durations_additive: false,
        events_truncated: false, unbound_event_count: 0, missing_measurements: ['server_first_text_ms', 'browser_first_text_ms'], stages: [],
        comparison: { window: monitorWindow, sample_count: 0, latency: {}, normal_run_id: null,
          scope: 'same_agent_skill_profile_model_configuration', missing_reason: 'no_comparable_successful_run' }
      });
      if (url.pathname === '/api/ai/admin/runs/run-failed/trajectory') return json({
        contract_version: 'ai-platform.admin-run-trajectory.v1', run_id: run.run_id, events: [], next_after_sequence: 0, has_more: false
      });
      if (url.pathname === '/api/ai/admin/model-providers/supplier-b') return json({
        providers: [{ id: 'legacy', name: '默认供应商', revision: 7, model_count: 29 }, { id: 'supplier-b', name: '供应商 B（合成）', revision: 8, model_count: 0 }],
        connection: { configured: true, revision: 8, base_url: 'https://supplier-b.example.invalid', key_fingerprint: 'synthetic' }, models: []
      });
      if (url.pathname === '/api/ai/admin/model-providers/legacy/impact') return json({ id: 'legacy', name: '默认供应商', revision: 7, model_count: 29, group_count: 1, default_group_count: 1, active_run_count: 0, cleanable_count: 28, blocked_models: [{id:'mdl-0',label:'Model 0',group_count:1,default_group_count:1,active_run_count:0,groups:[{name:'布局测试组'}]}] });
      if (url.pathname === '/api/ai/admin/models' || url.pathname === '/api/ai/admin/model-providers/legacy') return json({
        providers: [{ id: 'legacy', name: '默认供应商', revision: 7, model_count: 29 }, { id: 'supplier-b', name: '供应商 B（合成）', revision: 8, model_count: 0 }],
        connection: { configured: true, revision: 7, base_url: 'https://gateway.example.invalid', key_fingerprint: 'synthetic' },
        models: Array.from({ length: 29 }, (_, index) => ({
          id: 'mdl-' + index,
          value: index === 18 ? 'deepseek-v4-flash-cc' : 'demo/model-' + index,
          label: index === 18 ? 'deepseek-v4-flash-cc' : 'Model ' + index,
          provider: 'compatible', enabled: index < 2, available: true,
          order: index + 1, last_seen_revision: 7, last_seen_at: '2026-01-01T00:00:00Z',
          auto_compact_window: index < 2 ? 200000 : undefined,
          max_output_tokens: index < 2 ? 2048 : undefined
        }))
      });
      if (url.pathname === '/api/ai/models/groups') return json({
        groups: [{ id: 'group-layout', code: 'layout', name: '布局测试组', type: 'business', enabled: true, revision: 1, is_primary: true, availability: 'available', models: [{ id: 'model-enterprise', value: 'model-enterprise', label: 'Enterprise Claude', enabled: true }] }],
        count: 1, version: 'model-groups.v1'
      });
      if (url.pathname === '/api/ai/admin/model-groups') return json({
        groups: ${groupCase === "default" ? `[
          { id: 'group-default', code: 'default', name: '普通默认组', type: 'ordinary_default', enabled: true, revision: 1, remark: '', sort_order: 0, model_count: 0, member_count: 32, members_are_implicit: true },
          { id: 'group-layout', code: 'layout', name: '布局测试组', type: 'business', enabled: true, revision: 1, remark: '', sort_order: 1, model_count: 1, member_count: 1 }
        ]` : `[
          { id: 'group-layout', code: 'layout', name: '布局测试组', type: 'business', enabled: true, revision: 1, remark: '', sort_order: 1, model_count: 1, member_count: 1 }
        ]`}, version: 'model-groups.v1'
      });
      if (url.pathname === '/api/ai/admin/company-employees') {
        const offset = Number(url.searchParams.get('offset') || 0);
        const department = url.searchParams.get('department_id');
        const search = (url.searchParams.get('search') || '').toLowerCase();
        const employees = Array.from({ length: ${directorySize} }, (_, index) => ({ work_id: 'synthetic-' + index, display_name: '合成员工 ' + (index + 1), department_ids: [index < 103 ? '456' : '123'] }))
          .filter(employee => (!department || employee.department_ids.includes(department)) && (!search || (employee.work_id + ' ' + employee.display_name).toLowerCase().includes(search)));
        return json({ employees: employees.slice(offset, offset + 100), total: employees.length, offset, limit: 100, snapshot_id: 'synthetic-directory-v1' });
      }
      if (url.pathname === '/api/ai/admin/users') return json({
        schema_version: 'ai-platform.admin-user-diagnostics.v1', users: [{ user_id: 'layout-user', display_name: 'Layout Admin', status: 'active', session_count: 0, run_count: 0, queued_run_count: 0, running_run_count: 0, succeeded_run_count: 0, failed_run_count: 0, cancelled_run_count: 0 }], total: 1, offset: 0, limit: 50
      });
      if (url.pathname === '/api/ai/admin/users/layout-user/model-groups') return json({ user_id: 'layout-user', revision: 1, groups: [], primary_group_id: null, default_implicit: true });
      if (url.pathname === '/api/ai/admin/model-groups/group-default') return json({
        id: 'group-default', code: 'default', name: '普通默认组', type: 'ordinary_default', enabled: true, revision: 1, remark: '', sort_order: 0, model_count: 0, member_count: 32, members_are_implicit: true, members: [], models: []
      });
      if (url.pathname === '/api/ai/admin/model-groups/group-layout') return json({
        id: 'group-layout', code: 'layout', name: '布局测试组', type: 'business', enabled: true, revision: 1, remark: '', sort_order: 1, model_count: 1, member_count: 1,
        members: [{ user_id: 'layout-user', work_id: 'layout-user', display_name: 'Layout Admin', auth_source: 'company-login', is_primary: true }],
        models: [{ model_id: 'mdl-0', model_value: 'demo/model-0', label: 'Model 0', enabled: true, display_order: 0, priority: 100, weight: 100 }]
      });
      if (url.pathname === '/api/ai/files/long-name' && method === 'DELETE') return json({ key: 'long-name', deleted: true });
      if (url.pathname === '/api/agent/models/available') return json({
        models: [{ id: 'model-enterprise', value: 'model-enterprise', label: 'Enterprise Claude', provider: 'anthropic' }],
        count: 1, enabled_count: 1, default_model_id: 'model-enterprise'
      });
      if (url.pathname === '/api/ai/auth/company-credential-handoff') return json({ credential: 'synthetic-handoff' });
      if (url.pathname === '/api/profile-drive/status') return json({ status: ${chatFiles} ? 'connected' : 'disconnected', connected: ${chatFiles}, reauthRequired: false, connectedAtUtc: null, lastUsedAtUtc: null });
      if (${chatFiles} && url.pathname === '/api/profile-drive/files/list') return json({
        status: 'success', path: '', truncated: false,
        entries: Array.from({ length: 48 }, (_, index) => ({ path: 'report-' + index + '.txt', name: '报告 ' + (index + 1) + '.txt', type: 'file', size: 1200, lastModifiedUtc: '2026-01-01T00:00:00Z' }))
      });
      if (url.pathname === '/api/review/history') return json({ items: [] });
      if (url.pathname === '/api/review/stats/documents') return json({ total: 146, daily: [] });
      if (url.pathname === '/api/upload/config') return json({
        enabled: true,
        provider: 's3',
        maxFiles: 32,
        uploadLimitsBytes: {
          image: 20971520,
          video: 104857600,
          audio: 52428800,
          document: 52428800
        }
      });
      if (url.pathname === '/api/ai/chat/sessions/session-synthetic/files') return json({ session_id: 'session-synthetic', files: [{
        file_id: 'file-synthetic', run_id: 'run-synthetic-1', name: '已确认附件.pdf', mime_type: 'application/pdf', size_bytes: 2048,
        preview_url: '/api/ai/files/file-synthetic/preview?session_id=session-synthetic', download_url: '/api/ai/files/file-synthetic/download?session_id=session-synthetic'
      }] });
      if (url.pathname === '/api/ai/chat/sessions') return json({ sessions: [], next_cursor: null });
      if (url.pathname === '/api/ai/chat/follow-up-suggestions' && method === 'POST') return json({ suggestions: [] });
      if (url.pathname === '/api/sessions') return json({ sessions: [], total: 0, skip: 0, limit: 20, has_more: false });
      if (url.pathname.startsWith('/api/')) {
        state.errors.push('unstubbed_api:' + method + ':' + url.pathname);
        return json({ detail: 'route_layout_smoke_unstubbed_api' }, 501);
      }
      return originalFetch(input, init);
    };
  })()`;
}

const cases = [
  { path: "/scripts/fixtures/chat-integration.html", selector: "[data-agent-starter-prompts] button", name: "chat-integration", requiredSelectors: ["textarea", "[data-librechat-context-toggle]"] },
  { path: "/ai-apps/sop-assistant", selector: "textarea[aria-label=\"制度或流程问题\"]", name: "sop", scroller: "[data-ai-application-scroller]" },
  { path: "/ai-apps/word-review", selector: ".review-upload", name: "word", scroller: "[data-ai-application-scroller]", requiredRequests: ["/api/review/history", "/api/review/stats/documents"] },
  {
    path: "/",
    selector: ".welcome-root",
    name: "loading",
    loading: true,
    requiredSelectors: [".welcome-hero .w-48", ".welcome-hero .w-36"],
  },
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
      "[data-department-directory-selector] button[aria-haspopup=listbox]",
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
      "[data-agent-market-card] [data-agent-avatar-ref] img[src^='data:image/svg+xml']",
    ],
    requiredRequests: ["/api/ai/agent-profiles"],
  },
  {
    path: "/agent-market/agt_support",
    selector: "[data-agent-market-detail]",
    name: "market-detail",
    scroller: "[data-agent-market-detail]",
    requiredSelectors: [
      "[data-agent-market-start-chat]",
      "[data-agent-market-detail] [data-agent-avatar-ref] img[src^='data:image/svg+xml']",
    ],
    requiredRequests: ["/api/ai/agent-profiles/agt_support"],
  },
  {
    path: "/agent-market/agt_support/chat",
    selector: "[data-expert-workspace-header], [data-workbench-region='thread']",
    name: "market-workspace",
    requiredSelectors: [
      "[data-expert-workspace-header] [data-agent-avatar-ref] img[src^='data:image/svg+xml']",
      "textarea",
    ],
    requiredRequests: ["/api/ai/agent-profiles/agt_support"],
  },
  {
    path: "/agent-builder",
    selector: "[data-agent-builder-workbench]",
    name: "builder",
    scroller: "[data-agent-builder-workbench] > div:last-child",
    requiredSelectors: [
      "[data-agent-builder-workbench] input",
      "[data-agent-builder-save-reason]",
      "[data-agent-avatar-picker]",
      "[data-agent-avatar-picker] [data-agent-avatar-ref] img[src^='data:image/svg+xml']",
    ],
    requiredRequests: ["/api/ai/admin/agent-profiles", "/api/skills/", "/api/mcp/catalog-tools"],
  },
  {
    path: "/mcp",
    selector: "[data-mcp-view-tools]",
    name: "mcp",
    requiredSelectors: ["[data-mcp-view-tools]", "[data-mcp-tool-count]"],
    requiredRequests: ["/api/mcp/", "/api/mcp/synthetic-mcp/catalog"],
  },
  {
    path: "/plugins",
    selector: "[data-plugin-market]",
    name: "plugins",
    requiredSelectors: ["[data-plugin-card='personal-file-server']"],
    requiredRequests: ["/api/profile-drive/status"],
  },
  {
    path: "/runs", selector: "[data-run-monitor]", name: "runs",
    requiredSelectors: ['button[aria-label="查看 run-failed"]'],
    requiredRequests: ["/api/ai/admin/runs/monitor"],
  },
  {
    path: "/users",
    selector: "[data-workbench-projection-page=users]",
    name: "users",
    scroller: "[data-projection-list-panel]",
    requiredSelectors: ["[data-workbench-projection-page=users]", "[data-user-model-groups=layout-user]"],
    requiredRequests: ["/api/ai/admin/users"],
  },
  {
    path: "/models",
    selector: "[data-model-provider-management]",
    name: "models",
    scroller: (viewport) => viewport.width >= 1024 ? "[data-model-admin-table-scroll]" : "[data-model-catalog-shell]",
    requiredSelectors: ["[data-model-provider-row]", "[data-model-admin-tab=groups]"],
    requiredRequests: ["/api/ai/admin/models", "/api/ai/admin/model-groups"],
  },
];
const viewports = [
  { name: "wide", width: 1920, height: 900, mobile: false },
  { name: "desktop", width: 1440, height: 900, mobile: false },
  { name: "tablet", width: 768, height: 900, mobile: false },
  { name: "mobile", width: 390, height: 844, mobile: true },
];
const requestedCase = process.env.AI_PLATFORM_ROUTE_LAYOUT_CASE;
const requestedViewport = process.env.AI_PLATFORM_ROUTE_LAYOUT_VIEWPORT;
if (requestedViewport === "mobile-short") viewports.push({ name: "mobile-short", width: 390, height: 560, mobile: true });
if (requestedViewport === "desktop-short") viewports.push({ name: "desktop-short", width: 1440, height: 640, mobile: false });
const groupCase = process.env.AI_PLATFORM_ROUTE_LAYOUT_GROUP_CASE || "business";
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
    await browser.client.send("Page.addScriptToEvaluateOnNewDocument", { source: bootstrapSource(scenario.loading, ["chat-integration", "market-workspace"].includes(scenario.name)) });
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
    if (scenario.name === "users") {
      await browser.client.waitFor("Boolean(document.querySelector('button[data-user-model-groups=layout-user]'))", "users:group-trigger");
      await browser.client.evaluate("document.querySelector('button[data-user-model-groups=layout-user]').click()");
      await browser.client.waitFor("Boolean(document.querySelector('[data-user-model-groups=layout-user] [role=combobox]'))", "users:single-group-ready");
    }
    if (scenario.name === "builder") {
      await browser.client.evaluate(`(() => {
        const button = [...document.querySelectorAll('button')].find((node) => node.textContent.trim() === '新建专家');
        if (!button) throw new Error('builder_new_button_missing');
        button.click();
      })()`);
    }
    await browser.client.waitFor(
      `(${JSON.stringify(scenario.requiredSelectors ?? [])}).every((selector) => {
        const element = document.querySelector(selector);
        if (!element) return false;
        if (!(element instanceof HTMLImageElement)) return true;
        const rect = element.getBoundingClientRect();
        return element.complete && element.naturalWidth > 0 && element.naturalHeight > 0 && rect.width > 0 && rect.height > 0;
      })`,
      `${viewport.name}:${scenario.name}:required-controls`,
    );
    let marketView = null;
    if (scenario.name === "market") {
      marketView = await browser.client.evaluate(`(() => {
        const group = document.querySelector('[data-agent-market-view]');
        return [...group.querySelectorAll('button')].map(button => {
          const box = button.getBoundingClientRect();
          const icon = button.querySelector('svg').getBoundingClientRect();
          const style = getComputedStyle(button);
          return { label: button.getAttribute('aria-label'), width: box.width, height: box.height,
            iconWidth: icon.width, iconHeight: icon.height, paddingLeft: style.paddingLeft, paddingRight: style.paddingRight };
        });
      })()`);
      if (marketView.length !== 2 || marketView.some(button => button.width < 32 || button.height < 32 || button.iconWidth < 15 || button.iconHeight < 15)) {
        throw new Error(`market_view_toggle_size_failed:${JSON.stringify(marketView)}`);
      }
      await browser.client.evaluate(`document.querySelector('[aria-label="列表视图"]').click()`);
      await browser.client.waitFor(`document.querySelector('[aria-label="列表视图"]').getAttribute('aria-pressed') === 'true' && document.querySelector('[data-agent-market-card]').classList.contains('sm:flex-row')`, "market:list-view");
      await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-market-list`);
      await browser.client.evaluate(`document.querySelector('[aria-label="网格视图"]').focus()`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
      await browser.client.waitFor(`document.querySelector('[aria-label="网格视图"]').getAttribute('aria-pressed') === 'true' && document.querySelector('[data-agent-market-catalog]').classList.contains('grid')`, "market:grid-view-keyboard");
    }
    if (["market", "market-detail"].includes(scenario.name)) {
      const retiredCopyPresent = await browser.client.evaluate("document.body.textContent.includes('企业已发布')");
      if (retiredCopyPresent) throw new Error("market_retired_publication_badge_present");
    }
    let pluginLayout = null;
    if (scenario.name === "plugins") {
      if (viewport.mobile) {
        await browser.client.evaluate(`document.querySelector('[data-librechat-mobile-rail] button[aria-expanded=false]').click()`);
        await browser.client.waitFor(`document.querySelector('[data-librechat-mobile-sidebar]').getBoundingClientRect().left >= -1`, "plugins:mobile-sidebar-open");
      }
      const navigation = await browser.client.evaluate(`(() => {
        const shell = document.querySelector(${JSON.stringify(viewport.mobile ? "[data-librechat-mobile-sidebar]" : "[data-librechat-desktop-sidebar]")});
        return [...shell.querySelectorAll('[data-workbench-nav-item]')].map((button) => {
          const box = button.getBoundingClientRect();
          const icon = [...button.querySelectorAll('svg')].find((node) => node.getBoundingClientRect().width > 0).getBoundingClientRect();
          return { item: button.dataset.workbenchNavItem, inset: icon.left - box.left, iconWidth: icon.width, align: getComputedStyle(button).justifyContent };
        });
      })()`);
      const sidebarScreenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-plugins-sidebar`);
      if (viewport.mobile) {
        await browser.client.evaluate(`document.querySelector('[data-librechat-mobile-sidebar] button[title="收起侧边栏"]').click()`);
        await browser.client.waitFor(`document.querySelector('[data-librechat-mobile-sidebar]').getBoundingClientRect().right <= 1`, "plugins:mobile-sidebar-close");
      }
      await browser.client.evaluate(`(() => { const button = [...document.querySelectorAll('[data-plugin-card] button')].find((node) => node.textContent.trim() === '认证'); button.focus(); window.__pluginAuthTrigger = button; button.click(); })()`);
      await browser.client.waitFor("Boolean(document.querySelector('#file-server-password'))", "plugins:auth-open");
      // Wait for the real popup entrance animation before measuring its controls.
      await browser.client.waitFor(`(() => { const input = document.querySelector('#file-server-password'); return input && input.closest('[role=dialog]').getBoundingClientRect().height > 200 && [...document.querySelectorAll('[role=dialog]')].every((node) => node.getAnimations({ subtree: true }).every((animation) => animation.effect?.getTiming().iterations === Infinity || animation.playState !== 'running')); })()`, "plugins:auth-painted");
      const field = await browser.client.evaluate(`(() => {
        const input = document.querySelector('#file-server-password');
        const root = input.parentElement;
        const toggle = document.querySelector('button[aria-label="显示密码"]');
        const rect = root.getBoundingClientRect();
        const button = toggle.getBoundingClientRect();
        const dialog = input.closest('[role=dialog]').getBoundingClientRect();
        return { width: rect.width, height: rect.height, display: getComputedStyle(root).display,
          themed: getComputedStyle(root).borderTopStyle === 'solid' && (getComputedStyle(root).borderTopColor !== 'rgba(0, 0, 0, 0)' || getComputedStyle(root).backgroundColor !== 'rgba(0, 0, 0, 0)'),
          hasPrefix: Boolean(root.querySelector('svg')), toggleInside: root.contains(toggle), toggleWidth: button.width,
          iconWidth: toggle.querySelector('svg')?.getBoundingClientRect().width ?? 0,
          contained: dialog.left >= 0 && dialog.right <= innerWidth && dialog.top >= 0 && dialog.bottom <= innerHeight,
          focused: document.activeElement === input };
      })()`);
      const screenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-plugins-auth`);
      pluginLayout = { navigation, field, screenshot, sidebarScreenshot };
      if (!navigation.length || navigation.some((item) => item.inset > 24 || item.iconWidth < 18 || item.align !== 'flex-start') ||
          field.width < 260 || field.height < 40 || !field.themed || !field.hasPrefix || !field.toggleInside || field.toggleWidth < 28 || field.iconWidth < 15 || !field.contained || !field.focused) {
        throw new Error(`plugin_layout_failed:${JSON.stringify(pluginLayout)}`);
      }
      await browser.client.evaluate(`document.querySelector('[role=dialog] button[type=submit]').focus()`);
      for (const [modifiers, selector] of [[0, 'button[aria-label="关闭本地文件服务器认证"]'], [8, '[role=dialog] button[type=submit]']]) {
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, modifiers });
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, modifiers });
        await browser.client.waitFor(`document.activeElement === document.querySelector(${JSON.stringify(selector)})`, `plugins:auth-tab-wrap:${modifiers}`);
      }
      await browser.client.evaluate(`document.querySelector('button[aria-label="显示密码"]').click()`);
      await browser.client.waitFor(`document.querySelector('#file-server-password').type === 'text' && Boolean(document.querySelector('button[aria-label="隐藏密码"]'))`, "plugins:password-visible");
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor("!document.querySelector('#file-server-password') && document.activeElement === window.__pluginAuthTrigger", "plugins:auth-focus-restored");
      if (!viewport.mobile) {
        await browser.client.evaluate(`document.querySelector('[data-librechat-desktop-sidebar] button[title="收起侧边栏"]').click()`);
        await browser.client.waitFor("Boolean(document.querySelector('[data-librechat-desktop-sidebar] [data-workbench-rail-item]'))", "plugins:collapsed-rail");
        const centered = await browser.client.evaluate(`(() => { const button = document.querySelector('[data-librechat-desktop-sidebar] [data-workbench-rail-item]'); const box = button.getBoundingClientRect(); const icon = button.querySelector('svg').getBoundingClientRect(); return Math.abs((box.left + box.right - icon.left - icon.right) / 2) < 2; })()`);
        if (!centered) throw new Error('plugins:collapsed-icon-not-centered');
      }
    }
    let modelGroupDrawer = null;
    let providerScreenshot = null;
    if (scenario.name === "models") {
      assert.equal(await browser.client.evaluate(`document.querySelectorAll('[data-provider-expanded]').length`), 0, "providers start collapsed");
      await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-model-providers-collapsed`);
      await browser.client.evaluate(`document.querySelector('[aria-label="供应商 供应商 B（合成）"]').click()`);
      await browser.client.waitFor(`document.querySelector('[data-model-provider-row="supplier-b"] [data-provider-expanded]') && document.querySelector('[aria-label="模型 API 地址"]').value.includes('supplier-b')`, "models:provider-b-expanded");
      await browser.client.evaluate(`document.querySelector('[aria-label="供应商 默认供应商"]').click()`);
      await browser.client.waitFor(`document.querySelector('[data-model-provider-row="legacy"] [data-provider-expanded]') && document.querySelectorAll('[data-model-admin-model]').length === 29`, "models:provider-a-expanded");
      await browser.client.evaluate(`document.querySelector('[aria-label="供应商 默认供应商"]').scrollIntoView({block:'start'})`);
      await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-model-provider-rows`);
      await browser.client.evaluate(`([...document.querySelectorAll('button')].find(b => b.textContent.trim() === '新增供应商')).click()`);
      await browser.client.waitFor("Boolean(document.querySelector('input[aria-label=供应商名称]'))", "models:provider-create-form");
      providerScreenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-model-provider-create`);
      await browser.client.evaluate(`([...document.querySelectorAll('button')].find(b => b.textContent.trim() === '取消新增')).click()`);
      await browser.client.evaluate(`document.querySelector('[aria-label="删除供应商 默认供应商"]').click()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-provider-delete-blocked]'))", "models:provider-reference-block");
      assert.match(await browser.client.evaluate("document.querySelector('[data-provider-delete-blocked]').textContent"), /关联 1 个分组、1 个默认模型/);
      await browser.client.evaluate(`([...document.querySelectorAll('button')].find(b => b.textContent.trim() === '关闭检查结果')).click()`);
      const tabs = [];
      if (groupCase !== "default") await browser.client.evaluate(`(() => {
        const input = document.querySelector('[aria-label="启用 deepseek-v4-flash-cc"]');
        if (!input) throw new Error('synthetic_deepseek_missing');
        input.focus();
        input.click();
      })()`);
      if (groupCase !== "default") await browser.client.waitFor(
        `document.querySelectorAll('[data-model-admin-table-scroll] [role=switch][aria-checked=true]').length === 3`,
        `${viewport.name}:models:enabled-toggle`,
      );
      if (groupCase !== "default") {
        await browser.client.evaluate(`document.querySelector('[data-model-admin-edit="mdl-18"]').click()`);
        await browser.client.waitFor(`document.querySelector('[aria-label="deepseek-v4-flash-cc 最大输出 Token"]')?.getAttribute('placeholder') === '必填'`, `${viewport.name}:models:editor-limits`);
        await browser.client.evaluate(`document.querySelector('[aria-label="关闭模型编辑"]').click()`);
        await browser.client.waitFor(`!document.querySelector('[data-model-admin-editor]')`, `${viewport.name}:models:editor-closed`);
      }
      await browser.client.evaluate(`document.querySelector('[data-model-admin-tab="groups"]').click()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-model-group-management]'))", `${viewport.name}:models:groups-tab`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-model-group-table]'))", `${viewport.name}:models:groups-loaded`);
      const groupName = groupCase === "default" ? "普通默认组" : "布局测试组";
      await browser.client.waitFor(`Boolean(document.querySelector('[aria-label="编辑 ${groupName}"]'))`, `${viewport.name}:models:group-edit-ready`);
      await browser.client.evaluate(`document.querySelector('[aria-label="编辑 ${groupName}"]').click()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-model-group-models]'))", `${viewport.name}:models:drawer-loaded`);
      await browser.client.waitFor(`(() => {
        const drawer = document.querySelector('[data-model-group-drawer]');
        const panel = drawer?.querySelector('[data-drawer-placement]');
        return panel && Math.abs(panel.getBoundingClientRect().left - drawer.getBoundingClientRect().left) < 2;
      })()`, `${viewport.name}:models:drawer-settled`);
      const drawerTabs = groupCase === "default" ? ["基本信息", "模型"] : ["基本信息", "模型", "成员"];
      for (const tab of drawerTabs) {
        await browser.client.evaluate(`([...document.querySelectorAll('[data-model-group-drawer] [role=tab]')].find(node => node.textContent === ${JSON.stringify(tab)})).click()`);
        await browser.client.waitFor(`(() => {
          const drawer = document.querySelector('[data-model-group-drawer]');
          const panel = [...drawer.querySelectorAll('[role=tabpanel]')].find(node => node.checkVisibility());
          const rect = panel.getBoundingClientRect(); const frame = drawer.getBoundingClientRect();
          return Math.abs((rect.left - frame.left) - (frame.right - rect.right)) <= 2;
        })()`, `${viewport.name}:models:tab-position-settled:${tab}`);
        const geometry = await browser.client.evaluate(`(() => {
          const drawer = document.querySelector('[data-model-group-drawer]');
          const panel = [...drawer.querySelectorAll('[role=tabpanel]')].find(node => node.checkVisibility());
          const rect = panel.getBoundingClientRect();
          const frame = drawer.getBoundingClientRect();
          return { leftGap: rect.left - frame.left, rightGap: frame.right - rect.right,
            width: rect.width, scrollWidth: panel.scrollWidth, clientWidth: panel.clientWidth };
        })()`);
        tabs.push({ tab, ...geometry });
        if (Math.abs(geometry.leftGap - geometry.rightGap) > 2 || geometry.leftGap > 24 || geometry.scrollWidth > geometry.clientWidth + 1) {
          throw new Error(`model_group_drawer_width_failed:${viewport.name}:${tab}:${JSON.stringify(geometry)}`);
        }
      }
      await browser.client.evaluate(`([...document.querySelectorAll('[data-model-group-drawer] [role=tab]')].find(node => node.textContent === '模型')).click()`);
      if (groupCase === "default") await browser.client.waitFor("document.body.innerText.includes('普通默认组模型可供专家及独立业务入口') && document.body.innerText.includes('暂无已加入模型')", `${viewport.name}:models:default-empty-state`);
      const screenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-model-group-drawer`);
      let memberPicker = null;
      if (groupCase === "members") {
        await browser.client.evaluate(`([...document.querySelectorAll('[data-model-group-drawer] [role=tab]')].find(node => node.textContent === '成员')).click()`);
        await browser.client.evaluate(`document.querySelector('[aria-label="按组织架构选择人员"]').click()`);
        await browser.client.waitFor(`document.querySelector('[data-model-group-member-directory]')?.dataset.employeeTotal === '${directorySize}' && Boolean(document.querySelector('[aria-label="总公司 / 销售与客户支持事业部"]'))`, "members:all-pages");
        if (directorySize > 10000) {
          await browser.client.evaluate(`document.querySelector('[aria-label="全选全部人员"]').click()`);
          await browser.client.waitFor(`document.querySelector('.model-member-picker-selected').innerText.includes('已选 ${directorySize + 1} 人') && Boolean(document.querySelector('.model-member-picker-limit'))`, 'members:large-roster-selected');
          const rendered = await browser.client.evaluate(`({ employees:document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length, chips:document.querySelectorAll('.model-member-picker-chips button').length })`);
          if (rendered.employees > 30 || rendered.chips > 30) throw new Error('members:large-roster-unbounded-dom:' + JSON.stringify(rendered));
          await browser.client.evaluate(`([...document.querySelectorAll('.model-member-picker-selected button')].find(node => node.textContent === '清空')).click()`);
        }
        await browser.client.evaluate(`document.querySelector('[aria-label="总公司 / 销售与客户支持事业部"]').click()`);
        await browser.client.waitFor(`document.querySelector('[data-model-group-member-directory]')?.dataset.employeeTotal === '103'`, "members:department-selected");
        await browser.client.evaluate(`document.querySelector('[aria-label="全选本部门"]').click()`);
        await browser.client.waitFor(`document.querySelector('[aria-label="全选本部门"]').getAttribute('aria-checked') === 'true'`, "members:select-all-pages");
        await browser.client.evaluate(`document.querySelector('[data-model-group-member-directory] [role=checkbox]').click()`);
        await browser.client.waitFor(`document.querySelector('[aria-label="全选本部门"]').getAttribute('aria-checked') === 'mixed'`, "members:indeterminate");
        memberPicker = await browser.client.evaluate(`(() => { const popup = document.querySelector('.model-member-picker-panel'); const rect = popup.getBoundingClientRect(); const directory = popup.querySelector('[data-virtuoso-scroller]'); const footer = popup.querySelector('.model-member-picker-footer').getBoundingClientRect(); directory.scrollTop = directory.scrollHeight; return { contained: rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 && rect.bottom <= innerHeight + 1 && popup.scrollWidth <= popup.clientWidth + 1 && footer.bottom <= innerHeight, rows: Number(popup.querySelector('[data-model-group-member-directory]').dataset.employeeTotal), renderedRows: directory.querySelectorAll('[role=checkbox]').length, scrollReachable: directory.scrollTop + directory.clientHeight >= directory.scrollHeight - 2 }; })()`);
        await browser.client.waitFor(`Boolean(document.querySelector('[data-model-group-member-directory]').innerText.includes('synthetic-102'))`, "members:virtual-last-row");
        if (memberPicker.renderedRows >= memberPicker.rows) throw new Error('members:unbounded-render');
        memberPicker.screenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-organization-picker`);
        if (!memberPicker.contained || !memberPicker.scrollReachable) throw new Error(`member_picker_geometry:${JSON.stringify(memberPicker)}`);
        await browser.client.evaluate(`(() => { const input = document.querySelector('[aria-label="搜索公司员工"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'synthetic-${directorySize - 1}'); input.dispatchEvent(new Event('input', { bubbles:true })); })()`);
        await browser.client.waitFor(`document.querySelectorAll('[data-model-group-member-directory] [role=checkbox]').length === 1 && document.querySelector('[data-model-group-member-directory]').innerText.includes('synthetic-${directorySize - 1}')`, "members:company-search");
        await browser.client.evaluate(`document.querySelector('[aria-label="全选搜索结果"]').click()`);
        await browser.client.waitFor(`document.querySelector('[aria-label="全选搜索结果"]').getAttribute('aria-checked') === 'true'`, "members:select-search-results");
        memberPicker.searchScreenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-organization-search`);
        await browser.client.evaluate(`(() => { const input = document.querySelector('[aria-label="搜索公司员工"]'); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ''); input.dispatchEvent(new Event('input', { bubbles:true })); })()`);
        await browser.client.waitFor(`document.querySelector('[data-model-group-member-directory]')?.dataset.employeeTotal === '103' && document.querySelector('[aria-label="全选本部门"]').getAttribute('aria-checked') === 'mixed'`, "members:restore-department");
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
        await browser.client.waitFor(`!document.querySelector('[aria-label="公司组织架构选人"]') && Boolean(document.querySelector('[data-model-group-drawer]')) && document.activeElement?.getAttribute('aria-label') === '按组织架构选择人员' && document.querySelectorAll('[data-model-group-selected-member]').length === 1`, "members:escape-draft-focus");
      }
      modelGroupDrawer = { tabs, screenshot, memberPicker };
      if (groupCase === "delete") {
        await browser.client.evaluate(`([...document.querySelectorAll('[data-model-group-drawer] button')].find(node => node.textContent === '删除分组')).click()`);
        await browser.client.waitFor(`Boolean(document.querySelector('[aria-label="确认删除分组"] [role=combobox]'))`, "delete:confirmation");
        await browser.client.evaluate(`document.querySelector('[aria-label="确认删除分组"] [role=combobox]').focus()`);
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowDown", windowsVirtualKeyCode: 40 });
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowDown", windowsVirtualKeyCode: 40 });
        await browser.client.waitFor(`Boolean([...document.querySelectorAll('[role=option]')].find(node => node.checkVisibility()))`, "delete:destinations");
        await browser.client.evaluate(`([...document.querySelectorAll('[role=option]')].find(node => node.textContent.includes('确认全部回普通默认组'))).click()`);
        await browser.client.waitFor(`document.querySelector('[aria-label="确认删除分组"]').innerText.includes('确认删除即确认')`, "delete:fallback-warning");
        await browser.client.waitFor(`!document.querySelector('[role=listbox]')?.checkVisibility({ opacityProperty: true, visibilityProperty: true })`, "delete:selection-settled");
        const geometry = await browser.client.evaluate(`(() => { const modal = document.querySelector('[aria-label="确认删除分组"]'); const rect = modal.getBoundingClientRect(); const submit = [...modal.querySelectorAll('button')].find(node => node.textContent === '确认删除'); return { contained: rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 && rect.bottom <= innerHeight + 1, enabled: !submit.disabled }; })()`);
        geometry.screenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-model-group-delete`);
        if (!geometry.contained || !geometry.enabled) throw new Error(`delete_confirmation_failed:${JSON.stringify(geometry)}`);
        modelGroupDrawer.deletion = geometry;
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
        await browser.client.waitFor(`!document.querySelector('[aria-label="确认删除分组"]') && Boolean(document.querySelector('[data-model-group-drawer]')) && document.activeElement?.textContent === '删除分组'`, "delete:cancel-preserves-drawer");
      }
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor(`!document.querySelector('[data-model-group-drawer]') && document.activeElement?.getAttribute('aria-label') === '编辑 ${groupName}'`, `${viewport.name}:models:group-focus-restored`);
      await browser.client.evaluate(`document.querySelector('[data-model-admin-tab="catalog"]').click()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-model-admin-control]'))", `${viewport.name}:models:catalog-restored`);
    }
    const responsiveLayout = await browser.client.evaluate(`(() => {
      const detail = document.querySelector('[data-selected-skill-detail-shell]')?.getBoundingClientRect();
      const upload = document.querySelector('.review-upload')?.getBoundingClientRect();
      const header = document.querySelector('.review-panel-header');
      const table = document.querySelector('[data-mcp-server-row]')?.closest('table');
      let tableScroller = table?.parentElement;
      while (tableScroller && tableScroller.scrollWidth <= tableScroller.clientWidth + 1) tableScroller = tableScroller.parentElement;
      if (tableScroller) tableScroller.scrollLeft = tableScroller.scrollWidth;
      const tableReachable = !tableScroller || tableScroller.scrollLeft + tableScroller.clientWidth >= tableScroller.scrollWidth - 2;
      if (tableScroller) tableScroller.scrollLeft = 0;
      return {
        detailContained: !detail || (detail.left >= 0 && detail.right <= innerWidth + 1),
        uploadBottom: upload?.bottom ?? null,
        headerPosition: header ? getComputedStyle(header).position : null,
        tableWidth: table?.getBoundingClientRect().width ?? null, tableReachable,
        iconsValid: [...document.querySelectorAll('button svg')].filter(node => node.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && Number(node.getAttribute('width')) >= 12).every(node => node.getBoundingClientRect().width >= 12),
        badgesSingleLine: [...document.querySelectorAll('[data-governance-state]')].every(node => getComputedStyle(node).whiteSpace === 'nowrap'),
      };
    })()`);
    if (!responsiveLayout.detailContained || !responsiveLayout.iconsValid || !responsiveLayout.badgesSingleLine || !responsiveLayout.tableReachable ||
        (scenario.name === "mcp" && responsiveLayout.tableWidth < 1080) ||
        (scenario.name === "word" && (responsiveLayout.headerPosition === "sticky" || (viewport.mobile && responsiveLayout.uploadBottom > viewport.height)))) {
      throw new Error(`responsive_layout_failed:${viewport.name}:${scenario.name}:${JSON.stringify(responsiveLayout)}`);
    }
    const scrollerSelector = typeof scenario.scroller === "function" ? scenario.scroller(viewport) : scenario.scroller;
    const layout = await browser.client.evaluate(`(() => {
      const target = ${scrollerSelector ? `document.querySelector(${JSON.stringify(scrollerSelector)})` : "document.scrollingElement"};
      if (target) target.scrollTop = target.scrollHeight;
      const rect = target?.getBoundingClientRect();
      const requestedPaths = new Set(window.__routeLayoutSmoke.requests.map((request) => request.path));
      return {
        bodyScrollWidth: document.documentElement.scrollWidth,
        viewportWidth: window.innerWidth,
        userAvatarValid: [...document.querySelectorAll('[data-user-menu-trigger] [data-user-avatar]')].filter((avatar) => avatar.checkVisibility({ opacityProperty: true, visibilityProperty: true })).every((avatar) => {
          const box = avatar.getBoundingClientRect();
          const button = avatar.closest('button').getBoundingClientRect();
          return box.width >= 19 && box.height >= 19 && box.left >= button.left && box.right <= button.right + 1;
        }),
        railIconsValid: [...document.querySelectorAll('[data-workbench-rail-item] svg')].filter((icon) => icon.checkVisibility({ opacityProperty: true, visibilityProperty: true })).every((icon) => { const size = icon.getBoundingClientRect(); return size.width >= 18 && size.height >= 18; }),
        scrollTop: target?.scrollTop || 0,
        scrollHeight: target?.scrollHeight || 0,
        clientHeight: target?.clientHeight || 0,
        targetRect: rect ? { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom } : null,
        errors: window.__routeLayoutSmoke.errors,
        requests: [...requestedPaths],
      };
    })()`);
    if (scenario.name === "models" && groupCase !== "default") {
      await browser.client.evaluate(`document.querySelector('[data-model-admin-publish]').click()`);
      await browser.client.waitFor(
        `Boolean(document.querySelector('[data-model-admin-action-bar] [role="alert"]'))`,
        `${viewport.name}:models:missing-limits`,
      );
    }
    const missingRequests = (scenario.requiredRequests ?? []).filter(
      (path) => !layout.requests.includes(path),
    );
    const reachable = layout.scrollHeight <= layout.clientHeight + 1 || layout.scrollTop + layout.clientHeight >= layout.scrollHeight - 2;
    if (
      layout.bodyScrollWidth > layout.viewportWidth ||
      !layout.railIconsValid ||
      !layout.userAvatarValid ||
      !reachable ||
      layout.errors.length ||
      missingRequests.length
    ) {
      throw new Error(`layout_failed:${viewport.name}:${scenario.name}:${JSON.stringify({ layout, reachable, missingRequests })}`);
    }
    let modelLayout = null;
    if (scenario.name === "models" && groupCase !== "default") {
      if (viewport.width < 1024) await browser.client.evaluate(`document.querySelector('[data-model-admin-action-bar]').scrollIntoView({block:'end'})`);
      modelLayout = await browser.client.evaluate(`(() => {
        const table = document.querySelector('[data-model-admin-table-scroll]');
        const header = table.querySelector('thead').getBoundingClientRect();
        const tableRect = table.getBoundingClientRect();
        const bar = document.querySelector('[data-model-admin-action-bar]').getBoundingClientRect();
        const config = document.querySelector('[data-model-admin-control] > div:first-child').getBoundingClientRect();
        const shell = document.querySelector('[data-model-catalog-shell]');
        const filterLabel = [...document.querySelectorAll('label')].find((label) => label.textContent === '筛选模型状态');
        const filterControl = document.getElementById(filterLabel.htmlFor);
        const filter = filterControl.getBoundingClientRect();
        const search = document.querySelector('[aria-label="搜索模型"]').closest('label').getBoundingClientRect();
        return {
          filterGroupWidth: filterLabel.parentElement.parentElement.getBoundingClientRect().width,
          filterWidth: filter.width,
          searchWidth: search.width,
          fontsMatch: getComputedStyle(document.querySelector('[aria-label="搜索模型"]')).fontFamily === getComputedStyle(document.documentElement).fontFamily,
          rows: table.querySelectorAll('[data-model-admin-model]').length,
          enabledCount: table.querySelectorAll('[role=switch][aria-checked=true]').length,
          shellScrollTop: shell.scrollTop,
          configVisible: config.top >= -1 && config.bottom <= innerHeight,
          actionBottomGap: Math.round(document.querySelector('[data-provider-expanded]').getBoundingClientRect().bottom - bar.bottom),
          publishErrorVisible: document.querySelector('[data-model-admin-action-bar] [role="alert"]')?.textContent.includes('deepseek-v4-flash-cc') ?? false,
          publishRequestCount: window.__routeLayoutSmoke.requests.filter((request) => request.path === '/api/ai/admin/models/publish').length,
          tableScrollable: table.scrollHeight > table.clientHeight,
          headerPinned: Math.abs(header.top - tableRect.top) < 3 && header.bottom <= tableRect.bottom,
          headerOpaque: getComputedStyle(table.querySelector('th')).backgroundColor !== 'rgba(0, 0, 0, 0)',
          actionVisible: bar.top >= 0 && bar.bottom <= innerHeight + 1,
          actionBelowTable: bar.top >= tableRect.bottom - 2,
        };
      })()`);
      if ((viewport.width >= 640 && (Math.abs(modelLayout.filterWidth - 160) > 1 || Math.abs(modelLayout.searchWidth - Math.min(384, modelLayout.filterGroupWidth - 172)) > 1)) ||
        !modelLayout.fontsMatch || modelLayout.rows !== 29 || modelLayout.enabledCount !== 3 || !modelLayout.publishErrorVisible || modelLayout.publishRequestCount !== 0 || !modelLayout.actionVisible || !modelLayout.headerOpaque ||
        (viewport.width >= 1024 && (!modelLayout.tableScrollable || !modelLayout.headerPinned || !modelLayout.actionBelowTable || !modelLayout.configVisible || modelLayout.actionBottomGap > 48 || modelLayout.shellScrollTop !== 0)) ||
        (viewport.width < 1024 && layout.scrollHeight <= layout.clientHeight + 1)
      ) {
        throw new Error(`model_layout_failed:${viewport.name}:${JSON.stringify(modelLayout)}`);
      }
    }
    let avatarLayout = null;
    if (scenario.name === "builder") {
      avatarLayout = await browser.client.evaluate(`(() => {
        const picker = document.querySelector('[data-agent-avatar-picker]');
        const candidates = [...picker.querySelectorAll('button[aria-pressed]')].map((button) => {
          const frame = button.getBoundingClientRect();
          const avatar = button.querySelector('[data-agent-avatar-ref]').getBoundingClientRect();
          return {
            left: avatar.left, right: avatar.right, top: avatar.top, bottom: avatar.bottom,
            contained: avatar.left >= frame.left - 1 && avatar.right <= frame.right + 1 && avatar.top >= frame.top - 1 && avatar.bottom <= frame.bottom + 1,
          };
        });
        const directoryRows = [...document.querySelectorAll('[data-agent-directory-item]')].map((button) => {
          const row = button.getBoundingClientRect();
          const content = button.lastElementChild.getBoundingClientRect();
          const avatar = button.querySelector('[data-agent-avatar-ref]')?.getBoundingClientRect();
          return { top: row.top, bottom: row.bottom, contained: content.top >= row.top && content.bottom <= row.bottom + 1 && (!avatar || (avatar.top >= row.top && avatar.bottom <= row.bottom + 1)) };
        });
        return {
          directoryCount: directoryRows.length,
          directoryContained: directoryRows.every((row) => row.contained),
          directoryOverlapping: directoryRows.some((row, index) => index > 0 && directoryRows[index - 1].bottom > row.top + 1),
          count: candidates.length,
          contained: candidates.every((candidate) => candidate.contained),
          overlapping: candidates.some((candidate, index) => candidates.slice(index + 1).some((other) => candidate.left < other.right && candidate.right > other.left && candidate.top < other.bottom && candidate.bottom > other.top)),
        };
      })()`);
      if (avatarLayout.count !== 8 || !avatarLayout.contained || avatarLayout.overlapping || avatarLayout.directoryCount !== 2 || !avatarLayout.directoryContained || avatarLayout.directoryOverlapping) {
        throw new Error(`avatar_layout_failed:${viewport.name}:${JSON.stringify(avatarLayout)}`);
      }
    }
    let overlay = null;
    if (scenario.popover) {
      await browser.client.evaluate(`(() => { const node = document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]'); if (!node) throw new Error('department_trigger_missing'); node.click(); })()`);
      await browser.client.waitFor("Boolean(document.querySelector('[role=listbox]')) && document.querySelector('[data-department-directory-popup]').contains(document.activeElement) && document.querySelector('[data-department-directory-popup]').getAnimations({ subtree: true }).every((animation) => animation.playState !== 'running')", `${viewport.name}:department-menu`);
      overlay = await browser.client.evaluate(`(() => { const r = document.querySelector('[role=listbox]').getBoundingClientRect(); return { left:r.left, right:r.right, top:r.top, bottom:r.bottom, visible:r.left >= 0 && r.right <= innerWidth && r.top >= 0 && r.bottom <= innerHeight }; })()`);
      if (!overlay.visible) throw new Error(`overlay_clipped:${viewport.name}:${JSON.stringify(overlay)}`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      // Select can retain its closed animation DOM; verify the user-visible close contract.
      await browser.client.waitFor("!document.querySelector('[role=listbox]')?.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]').getAttribute('aria-expanded') === 'false' && document.activeElement === document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]')", `${viewport.name}:department-escape`);
      overlay.dismissal = await browser.client.evaluate(`(() => { const list = document.querySelector('[role=listbox]'); return { retainedDom: Boolean(list), visible: list?.checkVisibility({ opacityProperty: true, visibilityProperty: true }) ?? false, expanded: document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]').getAttribute('aria-expanded'), focusRestored: document.activeElement === document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]') }; })()`);
    }
    if (scenario.saveRequest) {
      await browser.client.evaluate(`(() => { const button = document.querySelector('[data-skill-distribution-save]'); if (!button) throw new Error('skill_distribution_save_missing'); button.click(); })()`);
      await browser.client.waitFor(
        `window.__routeLayoutSmoke.requests.some((request) => request.path === ${JSON.stringify(scenario.saveRequest)} && request.method === 'PUT')`,
        `${viewport.name}:skill-distribution-save`,
      );
      await browser.client.waitFor(`Boolean([...document.querySelectorAll('[data-skill-distribution-governance] [role=status]')].find((node) => node.textContent.includes('已保存')))`, `${viewport.name}:skill-distribution-receipt`);
    }
    let mcpFocus = null;
    if (scenario.name === "mcp") {
      await browser.client.evaluate(`(() => { const button = document.querySelector('[data-mcp-view-tools]'); button.focus(); button.click(); })()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-mcp-tool-detail] input[type=search]'))", `${viewport.name}:mcp:drawer`);
      await browser.client.evaluate(`document.querySelector('[data-mcp-tool-detail] input[type=search]').focus()`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
      await browser.client.waitFor("document.activeElement === document.querySelector('[data-mcp-tool-detail-close]')", `${viewport.name}:mcp:tab-wrap`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, modifiers: 8 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9, modifiers: 8 });
      await browser.client.waitFor("document.activeElement === document.querySelector('[data-mcp-tool-detail] input[type=search]')", `${viewport.name}:mcp:shift-tab-wrap`);
      await browser.client.waitFor(`(() => {
        const panel = document.querySelector('[data-mcp-tool-detail] [data-drawer-placement]').getBoundingClientRect();
        const close = document.querySelector('[data-mcp-tool-detail-close]').getBoundingClientRect();
        return panel.left >= 0 && panel.right <= innerWidth + 1 && close.left >= 0 && close.right <= innerWidth + 1;
      })()`, `${viewport.name}:mcp:drawer-contained`);
      const drawerScreenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-mcp-drawer`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor("!document.querySelector('[data-mcp-tool-detail]') && document.activeElement === document.querySelector('[data-mcp-view-tools]')", `${viewport.name}:mcp:focus-restore`);
      await browser.client.evaluate(`(() => { const button = document.querySelector('[data-mcp-admin-controls] button'); button.focus(); button.click(); })()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-mcp-editor-initial-focus]'))", `${viewport.name}:mcp:editor`);
      await browser.client.evaluate(`(() => { const label = [...document.querySelectorAll('[role=dialog] label')].find((node) => node.htmlFor && node.textContent === '传输类型'); if (!label) throw new Error('transport_label_missing'); document.getElementById(label.htmlFor).click(); })()`);
      await browser.client.waitFor("Boolean(document.querySelector('[role=listbox]'))", `${viewport.name}:mcp:transport-options`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor("!document.querySelector('[role=listbox]')?.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && Boolean(document.querySelector('[data-mcp-editor-initial-focus]'))", `${viewport.name}:mcp:nested-escape`);
      await browser.client.evaluate(`(() => { const trigger = document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]'); trigger.scrollIntoView({ block: 'center' }); trigger.click(); })()`);
      await browser.client.waitFor(`(() => { const option = document.querySelector('[data-department-directory-popup] [role=option]'); if (!option || option.getAttribute('aria-disabled') === 'true' || document.querySelector('[data-department-directory-popup]').getAnimations({ subtree: true }).some(animation => animation.playState === 'running')) return false; const rect = option.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth + 1 && rect.top >= 0 && rect.bottom <= innerHeight; })()`, `${viewport.name}:mcp:department-options`);
      const departmentScreenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-mcp-departments`);
      await browser.client.evaluate(`document.querySelector('[data-department-directory-popup] [role=option]').click()`);
      await browser.client.waitFor(`Boolean(document.querySelector('[data-department-selection-overflow] span[title="运营QA for 工程"]'))`, `${viewport.name}:mcp:department-selected`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor(`document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]').getAttribute('aria-expanded') === 'false' && !document.querySelector('[data-department-directory-popup]')?.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && document.activeElement === document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]') && Boolean(document.querySelector('[data-mcp-editor-initial-focus]'))`, `${viewport.name}:mcp:department-escape`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor("!document.querySelector('[data-mcp-editor-initial-focus]') && document.activeElement === document.querySelector('[data-mcp-admin-controls] button')", `${viewport.name}:mcp:editor-focus-restore`);
      mcpFocus = { drawerContained: true, tabWrap: true, shiftTabWrap: true, escapeClose: true, triggerFocusRestored: true, nestedEscapeIsolated: true, editorFocusRestored: true, drawerScreenshot, departmentScreenshot, departmentSelection: true };
    }
    let runDiagnostics = null;
    if (scenario.name === "runs") {
      await browser.client.evaluate(`(() => {
        const button = [...document.querySelectorAll('button[aria-label="查看 run-failed"]')].find(node => node.checkVisibility());
        button.scrollIntoView({ block: 'center' }); button.focus(); window.__runDiagnosticTrigger = button; button.click();
      })()`);
      await browser.client.waitFor("Boolean(document.querySelector('[data-run-runtime-diagnostics] summary'))", "runs:diagnostics-ready");
      await browser.client.waitFor(`(() => { const node = document.querySelector('[data-run-monitor-detail]'); const box = node.getBoundingClientRect(); return box.left >= 0 && box.right <= innerWidth + 1 && node.getAnimations({ subtree: true }).every(animation => animation.effect?.getTiming().iterations === Infinity || animation.playState !== 'running'); })()`, "runs:detail-painted");
      const collapsed = await browser.client.evaluate(`(() => {
        const details = [...document.querySelectorAll('[data-run-runtime-diagnostics] details')];
        return ['技术关联编号', '观察关联'].every(label => {
          const node = details.find(item => item.querySelector('summary')?.textContent === label);
          return node && !node.open && !node.querySelector('p').checkVisibility();
        });
      })()`);
      if (!collapsed) throw new Error("runs:observation-identities-not-collapsed");
      for (const label of ["技术关联编号", "观察关联"]) {
        await browser.client.evaluate(`(() => {
          const node = [...document.querySelectorAll('[data-run-runtime-diagnostics] summary')].find(item => item.textContent === ${JSON.stringify(label)});
          node.scrollIntoView({ block: 'center' }); node.focus();
        })()`);
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13, text: "\r", unmodifiedText: "\r" });
        await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
        await browser.client.waitFor(`[...document.querySelectorAll('[data-run-runtime-diagnostics] summary')].find(item => item.textContent === ${JSON.stringify(label)}).parentElement.open`, `runs:expand:${label}`);
      }
      runDiagnostics = await browser.client.evaluate(`(() => {
        const panel = document.querySelector('[data-run-monitor-detail]').getBoundingClientRect();
        const section = document.querySelector('[data-run-runtime-diagnostics]');
        const identities = [...section.querySelectorAll('details[open] p')];
        return { collapsed: ${collapsed}, contained: panel.left >= 0 && panel.right <= innerWidth + 1,
          idsContained: identities.every(node => { const box = node.getBoundingClientRect(); return box.left >= panel.left && box.right <= panel.right + 1; }),
          root: section.innerText.includes('观察 obs_' + '1'.repeat(32)),
          parent: section.innerText.includes('父观察 obs_' + '1'.repeat(32)),
          wrapper: section.innerText.includes('观察 obs_' + '2'.repeat(32)) };
      })()`);
      if (!runDiagnostics.contained || !runDiagnostics.idsContained || !runDiagnostics.root || !runDiagnostics.parent || !runDiagnostics.wrapper) {
        throw new Error(`runs:observation-chain-failed:${JSON.stringify(runDiagnostics)}`);
      }
      await browser.client.evaluate("document.querySelector('[data-run-runtime-diagnostics]').scrollIntoView({ block: 'center' })");
      runDiagnostics.screenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-runs-diagnostics`);
      await browser.client.evaluate(`(() => {
        const outer = [...document.querySelectorAll('[data-run-runtime-diagnostics] details')].find(node => node.querySelector('summary')?.textContent === '逐条观测证据');
        outer.open = true; outer.querySelector('details').open = true; outer.scrollIntoView({ block: 'center' });
      })()`);
      const receipt = await browser.client.evaluate(`(() => {
        const evidence = [...document.querySelectorAll('[data-run-runtime-diagnostics] pre')].find(node => node.textContent.includes('parent_observation_id'));
        return evidence?.checkVisibility() && evidence.textContent.includes('obs_' + '2'.repeat(32)) && evidence.textContent.includes('obs_' + '3'.repeat(32));
      })()`);
      if (!receipt) throw new Error("runs:receipt-parent-missing");
      runDiagnostics.receiptScreenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-runs-receipt`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor("!document.querySelector('[data-run-monitor-detail]') && document.activeElement === window.__runDiagnosticTrigger", "runs:focus-restored");
      runDiagnostics.receiptParent = true;
      runDiagnostics.focusRestored = true;
    }
    if (scenario.name === "builder") {
      await browser.client.evaluate(`(() => {
        document.querySelector('[data-agent-builder-mcp-settings]').open = true;
        const trigger = [...document.querySelectorAll('button')].find(node => node.textContent.trim() === '管理工具');
        trigger.focus();
        trigger.click();
      })()`);
      await browser.client.waitFor("Boolean(document.querySelector('[aria-label=\"搜索 MCP 工具\"]'))", "builder:mcp-search-open");
      const searchLayout = await browser.client.evaluate(`(() => {
        const dialog = document.querySelector('[role=dialog]');
        const input = dialog.querySelector('[aria-label="搜索 MCP 工具"]');
        const button = [...dialog.querySelectorAll('button')].find(node => node.textContent.trim() === '搜索');
        const box = dialog.getBoundingClientRect();
        return { contained: box.left >= 0 && box.right <= innerWidth && box.top >= 0 && box.bottom <= innerHeight,
          searchVisible: button?.getBoundingClientRect().width >= 40 && input.getBoundingClientRect().width >= 150,
          retiredCopyAbsent: !document.body.textContent.includes('Skill Set 无法覆盖的外部能力才需要额外选择 MCP 工具。') };
      })()`);
      if (!searchLayout.contained || !searchLayout.searchVisible || !searchLayout.retiredCopyAbsent) throw new Error(`builder_mcp_search_layout_failed:${JSON.stringify(searchLayout)}`);
      await browser.client.waitFor(`(() => {
        for (let node = document.querySelector('[role=dialog]'); node; node = node.parentElement) {
          if (Number(getComputedStyle(node).opacity) < 0.99) return false;
        }
        return true;
      })()`, "builder:mcp-search-painted");
      await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-builder-mcp-search`);
      await browser.client.evaluate(`(() => {
        const input = document.querySelector('[aria-label="搜索 MCP 工具"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '  UPLOAD_FILE  ');
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
      })()`);
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13 });
      await browser.client.waitFor("document.querySelector('[role=dialog]').textContent.includes('显示 1 / 3 项')", "builder:mcp-search-enter");
      await browser.client.evaluate(`document.querySelector('[role=dialog] label').click()`);
      await browser.client.waitFor("document.querySelector('[role=dialog]').textContent.includes('已选 1 项')", "builder:mcp-select-filtered");
      await browser.client.evaluate(`document.querySelector('[aria-label="清除 MCP 工具搜索"]').click()`);
      await browser.client.waitFor("document.querySelector('[role=dialog]').textContent.includes('已选 1 项 · 显示 3 / 3 项')", "builder:mcp-clear-preserves-selection");
      await browser.client.evaluate(`(() => {
        const input = document.querySelector('[aria-label="搜索 MCP 工具"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, 'missing-tool');
        input.dispatchEvent(new Event('input', { bubbles: true }));
      })()`);
      await browser.client.evaluate(`([...document.querySelectorAll('[role=dialog] button')].find(node => node.textContent.trim() === '搜索')).click()`);
      await browser.client.waitFor("document.querySelector('[role=dialog]').textContent.includes('没有匹配的 MCP 工具')", "builder:mcp-search-button-empty");
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Escape", code: "Escape", windowsVirtualKeyCode: 27 });
      await browser.client.waitFor("!document.querySelector('[role=dialog]') && document.activeElement?.textContent.trim() === '管理工具'", "builder:mcp-focus-restored");
      await browser.client.evaluate(`document.querySelector('[data-agent-avatar-picker]').scrollIntoView({ block: 'center' })`);
    }
    const chatIntegration = ["chat-integration", "market-workspace"].includes(scenario.name)
      ? await checkChatIntegration(browser.client, viewport, scenario.name, { theme, evidenceDir }) : null;
    const finalErrors = await browser.client.evaluate("window.__routeLayoutSmoke.errors");
    if (finalErrors.length) throw new Error(`interaction_failed:${viewport.name}:${scenario.name}:${JSON.stringify(finalErrors)}`);
    let loadingLayout = null;
    if (scenario.loading) {
      await browser.client.waitFor(`(() => {
        let node = document.querySelector('.welcome-hero .w-48');
        if (!node) return false;
        while (node) {
          if (Number(getComputedStyle(node).opacity) < 0.99) return false;
          node = node.parentElement;
        }
        return true;
      })()`, `${viewport.name}:loading:painted`);
      loadingLayout = await browser.client.evaluate(`(() => {
        const heading = document.querySelector('.welcome-hero .w-48');
        const subtitle = document.querySelector('.welcome-hero .w-36');
        const rect = heading.getBoundingClientRect();
        const secondary = subtitle.getBoundingClientRect();
        return { width: rect.width, height: rect.height, subtitleWidth: secondary.width, subtitleHeight: secondary.height,
          inlineDimensionsCleared: heading.style.width === '' && heading.style.height === '' && subtitle.style.width === '' && subtitle.style.height === '',
          visible: getComputedStyle(heading).backgroundColor !== 'rgba(0, 0, 0, 0)' };
      })()`);
      if (!loadingLayout.inlineDimensionsCleared || !loadingLayout.visible || loadingLayout.width <= 0 || loadingLayout.height <= 20 || loadingLayout.subtitleWidth <= 0 || loadingLayout.subtitleHeight <= 0) {
        throw new Error(`loading_layout_failed:${viewport.name}:${JSON.stringify(loadingLayout)}`);
      }
    }
    const screenshot = await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-${scenario.name}`);
    return { viewport: viewport.name, route: scenario.path, layout, reachable, responsiveLayout, modelLayout, modelGroupDrawer, providerScreenshot, avatarLayout, overlay, mcpFocus, runDiagnostics, loadingLayout, pluginLayout, marketView, chatIntegration, screenshot };
  } catch (error) {
    const focusDiagnostic = await browser.client.evaluate(`(() => ({ activeTag: document.activeElement?.tagName, activeLabel: document.activeElement?.getAttribute('aria-label'), activeTitle: document.activeElement?.getAttribute('title'), listboxes: [...document.querySelectorAll('[role=listbox]')].map(node => ({ visible: node.checkVisibility({ opacityProperty: true, visibilityProperty: true }), display: getComputedStyle(node).display, visibility: getComputedStyle(node).visibility, opacity: getComputedStyle(node).opacity, hidden: node.getAttribute('aria-hidden'), inert: Boolean(node.closest('[inert]')), ending: node.hasAttribute('data-ending-style') })), departmentExpanded: document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]')?.getAttribute('aria-expanded'), departmentTriggerFocused: document.activeElement === document.querySelector('[data-department-directory-selector] button[aria-haspopup=listbox]') }))()`);
    console.error(JSON.stringify({ route: scenario.path, focusDiagnostic }));
    if (scenario.loading) {
      const paint = await browser.client.evaluate(`(() => {
        const ancestors = [];
        let node = document.querySelector('.welcome-hero .w-48');
        while (node) {
          const style = getComputedStyle(node);
          ancestors.push({ tag: node.tagName, class: node.className, opacity: style.opacity, background: style.backgroundColor, visibility: style.visibility, animation: style.animation, animations: node.getAnimations().map((animation) => ({ state: animation.playState, time: animation.currentTime })) });
          node = node.parentElement;
        }
        return { readyState: document.readyState, ancestors };
      })()`);
      console.error(JSON.stringify({ loadingPaint: paint }));
    }
    await captureScreenshot(browser.client, evidenceDir, `${theme}-${viewport.name}-${scenario.name}-failed`).catch((captureError) => console.error(String(captureError)));
    throw error;
  } finally {
    await browser.close();
  }
}

const results = [];
const failures = [];
for (const viewport of selectedViewports) {
  for (const scenario of selectedCases) {
    try { results.push(await runCase(viewport, scenario)); }
    catch (error) {
      failures.push({ viewport: viewport.name, route: scenario.path, error: String(error) });
    }
  }
}
const evidence = { schema: "ai-platform.route-layout-browser-smoke.v1", mock_backed: true, theme, baseUrl, status: failures.length ? "failed" : "passed", results, failures };
mkdirSync(evidenceDir, { recursive: true });
writeFileSync(resolve(evidenceDir, "evidence.json"), `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify(evidence));
if (failures.length) process.exitCode = 1;
