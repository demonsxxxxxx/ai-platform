import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BadgeCheck,
  Bot,
  CircleAlert,
  Cpu,
  FileText,
  Plus,
  RefreshCw,
  Rocket,
  Save,
  ShieldAlert,
  Wrench,
  X,
} from "lucide-react";

import { AgentBuilderDialog } from "../../components/agent-builder/AgentBuilderDialog";
import type { ModelOption } from "../../services/api/modelPublic";
import type { PublicSkillResponse } from "../../types";
import { AgentBuilderEnterpriseFields } from "./AgentBuilderEnterpriseFields";
import { AgentBuilderLifecycle } from "./AgentBuilderLifecycle";
import {
  agentBuilderBlockReason,
  getAgentProfilePublishBlock,
  getAgentProfileSaveBlock,
  hasUnsavedAgentProfileEdits,
  isAgentProfileEditorDirty,
  type AgentBuilderCurrentCatalog,
  type AgentBuilderEditor,
  type AgentBuilderSafeMcpTool,
} from "./agentBuilderAdapter";
import { AgentBuilderController } from "./agentBuilderController";

export interface AgentBuilderWorkbenchCatalog {
  skills: readonly PublicSkillResponse[];
  tools: readonly AgentBuilderSafeMcpTool[];
  models: readonly ModelOption[];
  skillsResolved: boolean;
  mcpToolsResolved: boolean;
  modelsResolved: boolean;
  effectivePermissionsKnown: boolean;
  isLoading: boolean;
  error: string | null;
  retry: () => void;
}

export interface AgentBuilderWorkbenchProps {
  catalog: AgentBuilderWorkbenchCatalog;
  canManageProfiles?: boolean;
}

type PendingEditorAction =
  | { kind: "new" }
  | { kind: "profile"; agentId: string }
  | { kind: "refresh" };

function profileStatusLabel(status: "draft" | "published" | "withdrawn") {
  if (status === "published") return "已发布";
  if (status === "withdrawn") return "已下架";
  return "草稿";
}

function editorStatusLabel(editor: AgentBuilderEditor) {
  if (!editor.agentId) return "本地未保存";
  const base = editor.status ? profileStatusLabel(editor.status) : "状态未知";
  return isAgentProfileEditorDirty(editor) ? `${base} · 有未保存更改` : base;
}

function editorStatusTone(editor: AgentBuilderEditor) {
  if (!editor.agentId) {
    return "border-[var(--theme-border-strong)] bg-[var(--theme-hover)] text-[var(--theme-text-secondary)]";
  }
  if (editor.status === "published" && !isAgentProfileEditorDirty(editor)) {
    return "border-[var(--theme-success-ring)] bg-[var(--theme-success-soft)] text-[var(--theme-success)]";
  }
  return "border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] text-[var(--theme-warning)]";
}

/** Server-backed Chinese admin list/editor for immutable Agent Profile revisions. */
export function AgentBuilderWorkbench({
  catalog,
  canManageProfiles = false,
}: AgentBuilderWorkbenchProps) {
  const [controller] = useState(() => new AgentBuilderController());
  const [workbench, setWorkbench] = useState(controller.state);
  const [dialog, setDialog] = useState<"skills" | "tools" | null>(null);
  const [pendingEditorAction, setPendingEditorAction] = useState<PendingEditorAction | null>(null);
  const retryCatalog = catalog.retry;

  useEffect(() => controller.subscribe(setWorkbench), [controller]);
  useEffect(() => {
    if (!canManageProfiles) return;
    void controller.loadProfiles();
    return () => controller.cancelPending();
  }, [canManageProfiles, controller]);

  const currentCatalog = useMemo<AgentBuilderCurrentCatalog>(
    () => ({
      skills: catalog.skills,
      mcpTools: catalog.tools,
      models: catalog.models,
      skillsResolved: catalog.skillsResolved,
      mcpToolsResolved: catalog.mcpToolsResolved,
      modelsResolved: catalog.modelsResolved,
      effectivePermissionsKnown: catalog.effectivePermissionsKnown,
    }),
    [
      catalog.effectivePermissionsKnown,
      catalog.mcpToolsResolved,
      catalog.models,
      catalog.modelsResolved,
      catalog.skills,
      catalog.skillsResolved,
      catalog.tools,
    ],
  );

  const activeEditor = workbench.activeEditor;
  const saveBlock = getAgentProfileSaveBlock(activeEditor, currentCatalog);
  const publishBlock = getAgentProfilePublishBlock(activeEditor, currentCatalog);
  const mutationBusy =
    workbench.mutation.phase === "saving" ||
    workbench.mutation.phase === "publishing" ||
    workbench.mutation.phase === "unpublishing" ||
    workbench.mutation.phase === "testing";
  const interactionBusy = mutationBusy || workbench.destructiveReloadPending;
  const modelCatalogResolved = catalog.modelsResolved;
  const skillCatalogResolved = catalog.skillsResolved && catalog.effectivePermissionsKnown;
  const mcpCatalogResolved = catalog.mcpToolsResolved;
  const selectedModel = activeEditor && modelCatalogResolved
    ? catalog.models.find((model) => model.id === activeEditor.modelId)
    : undefined;
  const selectedSkill = activeEditor?.selectedSkill && skillCatalogResolved
    ? catalog.skills.find(
        (skill) =>
          skill.name === activeEditor.selectedSkill?.skill_id &&
          skill.expected_version === activeEditor.selectedSkill.expected_version,
      )
    : undefined;
  const unavailableMcpToolIds = activeEditor && mcpCatalogResolved
    ? activeEditor.selectedMcpToolIds.filter(
        (toolId) => !catalog.tools.some((tool) => tool.id === toolId),
      )
    : [];
  const mutationFeedback = workbench.mutation.phase === "error"
    ? { tone: "error" as const, message: workbench.mutation.error.message }
    : workbench.mutation.phase === "success"
      ? {
          tone: "success" as const,
          message: workbench.mutation.action === "save"
            ? `草稿已保存为服务端 revision ${workbench.mutation.revision}。`
            : workbench.mutation.action === "publish"
              ? `发布成功，当前服务端 revision 为 ${workbench.mutation.revision}。`
              : workbench.mutation.action === "unpublish"
                ? `已下架，当前服务端 revision 为 ${workbench.mutation.revision}。`
                : "受控测试运行已创建。",
        }
      : null;
  const canRecoverServerRevision = workbench.mutation.phase === "error" &&
    workbench.mutation.error.code === "agent_profile_revision_stale";

  const closeDialog = useCallback(() => setDialog(null), []);
  const performRefresh = useCallback((discardUnsavedChanges = false) => {
    retryCatalog();
    void controller.loadProfiles(discardUnsavedChanges);
  }, [controller, retryCatalog]);
  const refresh = useCallback(() => {
    if (activeEditor && hasUnsavedAgentProfileEdits(activeEditor)) {
      setPendingEditorAction({ kind: "refresh" });
      return;
    }
    performRefresh();
  }, [activeEditor, performRefresh]);
  const updateEditor = useCallback(
    (update: (editor: AgentBuilderEditor) => AgentBuilderEditor) => {
      controller.updateActiveEditor(update);
    },
    [controller],
  );
  const toggleMcpTool = useCallback(
    (toolId: string) => {
      updateEditor((editor) => ({
        ...editor,
        selectedMcpToolIds: editor.selectedMcpToolIds.includes(toolId)
          ? editor.selectedMcpToolIds.filter((selectedId) => selectedId !== toolId)
          : [...editor.selectedMcpToolIds, toolId],
      }));
    },
    [updateEditor],
  );
  const removeMcpTool = useCallback(
    (toolId: string) => {
      updateEditor((editor) => ({
        ...editor,
        selectedMcpToolIds: editor.selectedMcpToolIds.filter(
          (selectedId) => selectedId !== toolId,
        ),
      }));
    },
    [updateEditor],
  );
  const requestNewAgent = useCallback(() => {
    if (activeEditor?.agentId === null) return;
    if (activeEditor && hasUnsavedAgentProfileEdits(activeEditor)) {
      setPendingEditorAction({ kind: "new" });
      return;
    }
    controller.createNewAgent();
  }, [activeEditor, controller]);
  const requestProfile = useCallback((agentId: string) => {
    if (activeEditor?.agentId === agentId) return;
    if (activeEditor && hasUnsavedAgentProfileEdits(activeEditor)) {
      setPendingEditorAction({ kind: "profile", agentId });
      return;
    }
    controller.selectProfile(agentId);
  }, [activeEditor, controller]);
  const confirmEditorAction = useCallback(() => {
    const pending = pendingEditorAction;
    setPendingEditorAction(null);
    if (!pending) return;
    if (pending.kind === "refresh") {
      performRefresh(true);
      return;
    }
    if (pending.kind === "new") {
      controller.createNewAgent(true);
      return;
    }
    controller.selectProfile(pending.agentId, true);
  }, [controller, pendingEditorAction, performRefresh]);

  if (!canManageProfiles) {
    return (
      <main
        data-agent-builder-access-denied
        className="flex min-h-0 flex-1 items-center justify-center bg-[var(--theme-workbench-canvas)] px-4 text-[var(--theme-text)] sm:px-6"
      >
        <div className="flex max-w-md items-start gap-3 border-l-2 border-l-[var(--theme-danger)] py-2 pl-4">
          <ShieldAlert size={20} className="mt-0.5 shrink-0 text-[var(--theme-danger)]" aria-hidden="true" />
          <div>
            <h1 className="text-base font-semibold">仅管理员可访问智能体管理</h1>
            <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">
              当前账号没有管理智能体配置与版本的权限。
            </p>
          </div>
        </div>
      </main>
    );
  }

  return (
    <main
      data-agent-builder-workbench
      className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]"
    >
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-[var(--theme-border)] px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Bot size={20} className="shrink-0 text-[var(--theme-primary)]" aria-hidden="true" />
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">智能体管理</h1>
            <p className="text-sm text-[var(--theme-text-secondary)]">
              {workbench.listPhase === "loading"
                ? "正在同步服务端列表"
                : `${workbench.profiles.length} 个服务端智能体`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            aria-label="刷新智能体与授权目录"
            className="btn-secondary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={workbench.listPhase === "loading" || catalog.isLoading || interactionBusy}
            onClick={refresh}
            title="刷新智能体与授权目录"
            type="button"
          >
            <RefreshCw
              size={16}
              className={workbench.listPhase === "loading" || catalog.isLoading ? "animate-spin" : undefined}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">刷新</span>
          </button>
          <button
            className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={interactionBusy}
            onClick={requestNewAgent}
            type="button"
          >
            <Plus size={16} aria-hidden="true" />
            新建智能体
          </button>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[17rem_minmax(0,1fr)] lg:overflow-hidden">
        <aside className="max-h-72 overflow-y-auto overscroll-contain border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] lg:max-h-none lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between px-4 py-3">
            <h2 className="text-sm font-semibold">智能体列表</h2>
            <span className="text-xs tabular-nums text-[var(--theme-text-secondary)]">
              {workbench.profiles.length}
            </span>
          </div>

          {workbench.listError ? (
            <div className="mx-3 mb-3 border-l-2 border-l-[var(--theme-danger)] bg-[var(--theme-danger-soft)] px-3 py-2 text-sm text-[var(--theme-danger)]" role="alert">
              <p>{workbench.listError.message}</p>
              <button className="mt-2 text-sm font-medium underline" onClick={refresh} type="button">
                重新加载
              </button>
            </div>
          ) : null}

          {workbench.listPhase === "loading" && workbench.profiles.length === 0 ? (
            <p className="border-t border-[var(--theme-border)] px-4 py-4 text-sm text-[var(--theme-text-secondary)]">
              正在加载智能体…
            </p>
          ) : null}

          <div className="border-t border-[var(--theme-border)]">
            {workbench.localEditor ? (
              <button
                aria-pressed={activeEditor?.agentId === null}
                className={`flex w-full items-start gap-3 border-b border-l-2 border-b-[var(--theme-border)] border-l-[var(--theme-border-strong)] px-4 py-3 text-left disabled:cursor-not-allowed disabled:opacity-60 ${activeEditor?.agentId === null ? "bg-[var(--theme-hover)]" : "hover:bg-[var(--theme-hover)]"}`}
                disabled={interactionBusy}
                onClick={requestNewAgent}
                type="button"
              >
                <Bot size={16} className="mt-0.5 shrink-0 text-[var(--theme-text-secondary)]" aria-hidden="true" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">
                    {workbench.localEditor.name.trim() || "未命名智能体"}
                  </span>
                  <span className="mt-1 block text-xs text-[var(--theme-text-secondary)]">
                    本地未保存
                  </span>
                </span>
              </button>
            ) : null}

            {workbench.profiles.map((profile) => {
              const selected = activeEditor?.agentId === profile.agent_id;
              return (
                <button
                  key={profile.agent_id}
                  aria-label={`编辑智能体 ${profile.name}，${profileStatusLabel(profile.status)}，revision ${profile.revision}`}
                  aria-pressed={selected}
                  className={`flex w-full items-start gap-3 border-b border-l-2 border-b-[var(--theme-border)] px-4 py-3 text-left disabled:cursor-not-allowed disabled:opacity-60 ${profile.status === "published" ? "border-l-[var(--theme-success)]" : "border-l-[var(--theme-warning)]"} ${selected ? "bg-[var(--theme-hover)]" : "hover:bg-[var(--theme-hover)]"}`}
                  disabled={interactionBusy}
                  onClick={() => requestProfile(profile.agent_id)}
                  type="button"
                >
                  <Bot size={16} className="mt-0.5 shrink-0 text-[var(--theme-text-secondary)]" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{profile.name}</span>
                    <span className="mt-1 flex items-center justify-between gap-2 text-xs text-[var(--theme-text-secondary)]">
                      <span>{profileStatusLabel(profile.status)}</span>
                      <span className="tabular-nums">revision {profile.revision}</span>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>

          {workbench.listPhase === "ready" && workbench.profiles.length === 0 && !workbench.localEditor ? (
            <div className="px-4 py-6 text-sm text-[var(--theme-text-secondary)]">
              <p>当前没有服务端智能体。</p>
              <button className="btn-secondary mt-3 inline-flex items-center gap-2" onClick={requestNewAgent} type="button">
                <Plus size={16} aria-hidden="true" />
                新建智能体
              </button>
            </div>
          ) : null}
        </aside>

        <section className="min-w-0 lg:overflow-y-auto">
          {!activeEditor ? (
            <div className="flex min-h-72 items-center justify-center px-6 py-12">
              <div className="max-w-sm border-l-2 border-l-[var(--theme-primary)] py-2 pl-4">
                <h2 className="text-base font-semibold">尚未选择智能体</h2>
                <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">
                  从服务端列表选择一个智能体，或新建智能体。
                </p>
              </div>
            </div>
          ) : (
            <div className="mx-auto w-full max-w-4xl px-4 py-5 sm:px-6 lg:py-7">
              <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--theme-border)] pb-5">
                <div className="min-w-0">
                  <h2 className="truncate text-xl font-semibold">
                    {activeEditor.name.trim() || "未命名智能体"}
                  </h2>
                  <p className="mt-1 text-sm text-[var(--theme-text-secondary)]">
                    {activeEditor.agentId ?? "尚未分配服务端 agent_id"}
                  </p>
                </div>
                <span className={`rounded-md border px-2.5 py-1 text-xs font-medium ${editorStatusTone(activeEditor)}`}>
                  {editorStatusLabel(activeEditor)}
                </span>
              </div>

              {catalog.error ? (
                <div className="mt-5 flex items-start gap-2 border-l-2 border-l-[var(--theme-warning)] bg-[var(--theme-warning-soft)] px-3 py-2 text-sm text-[var(--theme-warning)]" role="status">
                  <CircleAlert size={17} className="mt-0.5 shrink-0" aria-hidden="true" />
                  <span>{catalog.error}</span>
                </div>
              ) : null}
              <section aria-labelledby="agent-basic-heading" className="border-b border-[var(--theme-border)] py-6">
                <div className="mb-4 flex items-center gap-2">
                  <Bot size={17} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
                  <h3 id="agent-basic-heading" className="text-sm font-semibold">基本信息</h3>
                </div>
                <div className="grid grid-cols-1 gap-4">
                  <label className="flex flex-col gap-2">
                    <span className="text-sm font-medium">名称</span>
                    <input
                      aria-label="智能体名称"
                      className="h-10 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)]"
                      disabled={interactionBusy}
                      onChange={(event) => updateEditor((editor) => ({ ...editor, name: event.target.value }))}
                      required
                      value={activeEditor.name}
                    />
                  </label>
                  <label className="flex flex-col gap-2">
                    <span className="text-sm font-medium">简介</span>
                    <textarea
                      aria-label="智能体简介"
                      className="min-h-20 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)]"
                      disabled={interactionBusy}
                      onChange={(event) => updateEditor((editor) => ({ ...editor, description: event.target.value }))}
                      value={activeEditor.description}
                    />
                  </label>
                </div>
              </section>

              <AgentBuilderEnterpriseFields
                disabled={interactionBusy}
                editor={activeEditor}
                onChange={(patch) =>
                  updateEditor((editor) => ({
                    ...editor,
                    ...patch,
                  }))
                }
              />

              <section aria-labelledby="agent-instructions-heading" className="border-b border-[var(--theme-border)] py-6">
                <div className="mb-4 flex items-center gap-2">
                  <FileText size={17} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
                  <h3 id="agent-instructions-heading" className="text-sm font-semibold">系统说明</h3>
                </div>
                <textarea
                    aria-label="智能体系统说明"
                  className="min-h-48 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm leading-6 outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)]"
                  disabled={interactionBusy}
                  onChange={(event) => updateEditor((editor) => ({ ...editor, instructions: event.target.value }))}
                  required
                  value={activeEditor.instructions}
                />
              </section>

              <section aria-labelledby="agent-model-heading" className="border-b border-[var(--theme-border)] py-6">
                <div className="mb-4 flex items-center gap-2">
                  <Cpu size={17} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
                  <h3 id="agent-model-heading" className="text-sm font-semibold">模型</h3>
                </div>
                <label className="flex max-w-xl flex-col gap-2">
                  <span className="text-sm font-medium">当前模型</span>
                  <select
                    aria-label="智能体模型"
                    className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm outline-none focus:border-[var(--theme-primary)] focus:ring-1 focus:ring-[var(--theme-primary)]"
                    disabled={catalog.isLoading || !modelCatalogResolved || interactionBusy}
                    onChange={(event) => updateEditor((editor) => ({ ...editor, modelId: event.target.value }))}
                    required
                    value={selectedModel ? activeEditor.modelId : ""}
                  >
                    <option value="">
                      {!modelCatalogResolved
                        ? "模型目录尚未完整加载"
                        : activeEditor.modelId && !selectedModel
                          ? "请重新选择当前可用模型"
                          : "选择模型"}
                    </option>
                    {catalog.models.map((model) => (
                      <option key={model.id} value={model.id}>{model.label}</option>
                    ))}
                  </select>
                </label>
                {activeEditor.modelId && !modelCatalogResolved ? (
                  <p className="mt-2 break-all text-sm text-[var(--theme-text-secondary)]">
                    模型目录尚未完整加载，已保留服务端模型 {activeEditor.modelId}。
                  </p>
                ) : activeEditor.modelId && !selectedModel ? (
                  <p className="mt-2 text-sm text-[var(--theme-danger)]">
                    已保存模型 <span className="font-mono">{activeEditor.modelId}</span> 当前不可用，未自动替换。
                  </p>
                ) : null}
              </section>

              <section aria-labelledby="agent-skill-heading" className="border-b border-[var(--theme-border)] py-6">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <BadgeCheck size={17} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
                    <h3 id="agent-skill-heading" className="text-sm font-semibold">主 Skill</h3>
                  </div>
                  <button className="btn-secondary disabled:cursor-not-allowed disabled:opacity-60" disabled={interactionBusy} onClick={() => setDialog("skills")} type="button">
                    选择主 Skill
                  </button>
                </div>
                {activeEditor.selectedSkill ? (
                  <div className={`border-l-2 py-1 pl-3 ${!skillCatalogResolved ? "border-l-[var(--theme-border-strong)]" : selectedSkill ? "border-l-[var(--theme-primary)]" : "border-l-[var(--theme-danger)]"}`}>
                    <p className="text-sm font-medium">{activeEditor.selectedSkill.skill_id}</p>
                    <p className="mt-1 break-all text-xs text-[var(--theme-text-secondary)]">
                      固定版本 {activeEditor.selectedSkill.expected_version}
                    </p>
                    {!skillCatalogResolved ? (
                      <p className="mt-2 text-sm text-[var(--theme-text-secondary)]">
                        授权 Skill 目录尚未完整加载，已保留服务端固定版本。
                      </p>
                    ) : !selectedSkill ? (
                      <p className="mt-2 text-sm text-[var(--theme-danger)]">
                        当前授权目录中没有这一精确版本，未自动回退。
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <p className="text-sm text-[var(--theme-text-secondary)]">未选择主 Skill</p>
                )}
                <p className="mt-3 text-xs leading-5 text-[var(--theme-text-secondary)]">
                  一个智能体固定一个主 Skill；该 Skill 声明的依赖会由系统自动装载。
                </p>
              </section>

              <section aria-labelledby="agent-mcp-heading" className="border-b border-[var(--theme-border)] py-6">
                <div className="mb-4 flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2">
                    <Wrench size={17} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
                    <h3 id="agent-mcp-heading" className="text-sm font-semibold">MCP 工具</h3>
                  </div>
                  <button className="btn-secondary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60" disabled={interactionBusy} onClick={() => setDialog("tools")} type="button">
                    <Wrench size={15} aria-hidden="true" />
                    管理工具
                  </button>
                </div>
                {activeEditor.selectedMcpToolIds.length === 0 ? (
                  <p className="text-sm text-[var(--theme-text-secondary)]">未选择 MCP 工具</p>
                ) : (
                  <div className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
                    {activeEditor.selectedMcpToolIds.map((toolId) => {
                      const tool = mcpCatalogResolved
                        ? catalog.tools.find((entry) => entry.id === toolId)
                        : undefined;
                      const toolUnavailable = mcpCatalogResolved && !tool;
                      return (
                        <div key={toolId} className="flex items-start gap-3 py-3">
                          <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${!mcpCatalogResolved ? "bg-[var(--theme-border-strong)]" : tool ? "bg-[var(--theme-success)]" : "bg-[var(--theme-danger)]"}`} aria-hidden="true" />
                          <span className="min-w-0 flex-1">
                            <span className="block break-all text-sm font-medium">{tool?.label ?? toolId}</span>
                            <span className={`mt-1 block text-xs ${toolUnavailable ? "text-[var(--theme-danger)]" : "text-[var(--theme-text-secondary)]"}`}>
                              {!mcpCatalogResolved
                                ? "授权 MCP 目录尚未完整加载，已保留服务端工具身份。"
                                : tool?.description ?? "当前授权目录中不可用，未自动移除。"}
                            </span>
                          </span>
                          <button
                            aria-label={`移除 MCP 工具 ${toolId}`}
                            className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md text-[var(--theme-text-secondary)] hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)] disabled:cursor-not-allowed disabled:opacity-60"
                            disabled={interactionBusy}
                            onClick={() => removeMcpTool(toolId)}
                            title={`移除 ${tool?.label ?? toolId}`}
                            type="button"
                          >
                            <X size={16} aria-hidden="true" />
                          </button>
                        </div>
                      );
                    })}
                  </div>
                )}
                {unavailableMcpToolIds.length > 0 ? (
                  <p className="mt-3 text-sm text-[var(--theme-danger)]">
                    {unavailableMcpToolIds.length} 项已选工具需要明确移除或重新授权。
                  </p>
                ) : null}
              </section>

              <section aria-labelledby="agent-version-heading" className="py-6">
                <div className="mb-4 flex items-center gap-2">
                  <Rocket size={17} className="text-[var(--theme-text-secondary)]" aria-hidden="true" />
                  <h3 id="agent-version-heading" className="text-sm font-semibold">状态与版本</h3>
                </div>
                <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <dt className="text-[var(--theme-text-secondary)]">状态</dt>
                    <dd className="mt-1 font-medium">{editorStatusLabel(activeEditor)}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--theme-text-secondary)]">revision</dt>
                    <dd className="mt-1 font-medium tabular-nums">{activeEditor.revision ?? "未分配"}</dd>
                  </div>
                  <div>
                    <dt className="text-[var(--theme-text-secondary)]">MCP 工具</dt>
                    <dd className="mt-1 font-medium tabular-nums">{activeEditor.selectedMcpToolIds.length}</dd>
                  </div>
                </dl>

                <div className="mt-5 flex flex-wrap items-center gap-2">
                  <button
                    aria-describedby={saveBlock ? "agent-builder-save-reason" : undefined}
                    className="btn-secondary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={interactionBusy || saveBlock !== null}
                    onClick={() => void controller.saveActiveProfile(currentCatalog)}
                    title={saveBlock ? agentBuilderBlockReason(saveBlock) : "保存草稿"}
                    type="button"
                  >
                    {workbench.mutation.phase === "saving" ? <RefreshCw size={16} className="animate-spin" aria-hidden="true" /> : <Save size={16} aria-hidden="true" />}
                    {workbench.mutation.phase === "saving" ? "保存中" : "保存草稿"}
                  </button>
                  <button
                    aria-describedby={publishBlock ? "agent-builder-publish-reason" : undefined}
                    className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
                    disabled={interactionBusy || publishBlock !== null}
                    onClick={() => void controller.publishActiveProfile(currentCatalog)}
                    title={publishBlock ? agentBuilderBlockReason(publishBlock) : "发布当前草稿"}
                    type="button"
                  >
                    {workbench.mutation.phase === "publishing" ? <RefreshCw size={16} className="animate-spin" aria-hidden="true" /> : <Rocket size={16} aria-hidden="true" />}
                    {workbench.mutation.phase === "publishing" ? "发布中" : "发布"}
                  </button>
                </div>
                <div aria-live="polite" className="mt-3 min-h-5">
                  {mutationFeedback ? (
                    <div
                      className={`flex items-start gap-2 text-sm ${mutationFeedback.tone === "error" ? "text-[var(--theme-danger)]" : "text-[var(--theme-success)]"}`}
                      role={mutationFeedback.tone === "error" ? "alert" : "status"}
                    >
                      {mutationFeedback.tone === "error"
                        ? <CircleAlert size={17} className="mt-0.5 shrink-0" aria-hidden="true" />
                        : <BadgeCheck size={17} className="mt-0.5 shrink-0" aria-hidden="true" />}
                      <span className="min-w-0 flex-1">{mutationFeedback.message}</span>
                      {canRecoverServerRevision ? (
                        <button
                          className="shrink-0 font-medium underline"
                          onClick={refresh}
                          type="button"
                        >
                          加载服务端版本
                        </button>
                      ) : null}
                    </div>
                  ) : null}
                </div>
                <div className="mt-3 space-y-1 text-xs text-[var(--theme-text-secondary)]">
                  {saveBlock ? <p data-agent-builder-save-reason id="agent-builder-save-reason">保存：{agentBuilderBlockReason(saveBlock)}</p> : null}
                  {publishBlock ? <p data-agent-builder-publish-reason id="agent-builder-publish-reason">发布：{agentBuilderBlockReason(publishBlock)}</p> : null}
                </div>
              </section>

              <AgentBuilderLifecycle
                disabled={interactionBusy}
                editor={activeEditor}
                mutation={workbench.mutation}
                onRunTest={(message) => void controller.runActiveProfileTest(message)}
                onUnpublish={() => void controller.unpublishActiveProfile()}
              />
            </div>
          )}
        </section>
      </div>

      <AgentBuilderDialog isOpen={dialog === "skills"} onClose={closeDialog} title="选择主 Skill">
        {!skillCatalogResolved ? (
          <p className="text-sm text-[var(--theme-text-secondary)]">授权 Skill 目录尚未完整加载。</p>
        ) : catalog.skills.length === 0 ? (
          <p className="text-sm text-[var(--theme-text-secondary)]">当前没有可选的已授权 Skill。</p>
        ) : (
          <div className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
            {catalog.skills.map((skill) => (
              <button
                key={`${skill.name}:${skill.expected_version}`}
                className="flex w-full flex-col items-start justify-between gap-2 px-1 py-3 text-left hover:bg-[var(--theme-hover)] disabled:cursor-not-allowed disabled:opacity-60 sm:flex-row sm:gap-4"
                disabled={interactionBusy}
                onClick={() => {
                  updateEditor((editor) => ({
                    ...editor,
                    selectedSkill: {
                      skill_id: skill.name,
                      expected_version: skill.expected_version,
                    },
                  }));
                  closeDialog();
                }}
                type="button"
              >
                <span className="min-w-0">
                  <span className="block font-medium">{skill.name}</span>
                  <span className="mt-1 block text-sm text-[var(--theme-text-secondary)]">{skill.description}</span>
                </span>
                <span className="break-all text-xs text-[var(--theme-text-secondary)] sm:shrink-0">
                  {skill.expected_version}
                </span>
              </button>
            ))}
          </div>
        )}
      </AgentBuilderDialog>

      <AgentBuilderDialog isOpen={dialog === "tools"} onClose={closeDialog} title="管理 MCP 工具">
        {!mcpCatalogResolved ? (
          <p className="text-sm text-[var(--theme-text-secondary)]">授权 MCP 目录尚未完整加载。</p>
        ) : catalog.tools.length === 0 ? (
          <p className="text-sm text-[var(--theme-text-secondary)]">当前没有可选的 MCP 工具。</p>
        ) : (
          <div className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
            {catalog.tools.map((tool) => (
              <label key={tool.id} className="flex cursor-pointer items-start gap-3 px-1 py-3 hover:bg-[var(--theme-hover)]">
                <input
                  checked={activeEditor?.selectedMcpToolIds.includes(tool.id) ?? false}
                  disabled={interactionBusy}
                  onChange={() => toggleMcpTool(tool.id)}
                  type="checkbox"
                />
                <span className="min-w-0">
                  <span className="block font-medium">{tool.label}</span>
                  <span className="mt-1 block text-sm text-[var(--theme-text-secondary)]">{tool.description}</span>
                </span>
              </label>
            ))}
          </div>
        )}
      </AgentBuilderDialog>

      <AgentBuilderDialog
        isOpen={pendingEditorAction !== null}
        onClose={() => setPendingEditorAction(null)}
        title={pendingEditorAction?.kind === "refresh" ? "放弃本地更改并刷新？" : "放弃未保存更改？"}
      >
        <div className="flex items-start gap-3">
          <CircleAlert size={19} className="mt-0.5 shrink-0 text-[var(--theme-warning)]" aria-hidden="true" />
          <p className="text-sm leading-6 text-[var(--theme-text-secondary)]">
            {pendingEditorAction?.kind === "refresh"
              ? "当前智能体有未保存的更改。仅在服务端列表成功返回后，才会加载最新服务端版本并放弃这些更改。"
              : "当前智能体有未保存的更改。切换后这些更改将无法恢复。"}
          </p>
        </div>
        <div className="mt-6 flex flex-wrap justify-end gap-2">
          <button
            autoFocus
            className="btn-secondary"
            onClick={() => setPendingEditorAction(null)}
            type="button"
          >
            继续编辑
          </button>
          <button
            className="btn-secondary border-[var(--theme-danger)] text-[var(--theme-danger)]"
            onClick={confirmEditorAction}
            type="button"
          >
            {pendingEditorAction?.kind === "refresh" ? "放弃本地更改并加载服务端版本" : "放弃并切换"}
          </button>
        </div>
      </AgentBuilderDialog>
    </main>
  );
}
