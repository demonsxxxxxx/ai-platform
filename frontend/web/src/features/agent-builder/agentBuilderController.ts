import {
  agentProfileApi,
  type AgentProfileTrialRunResponse,
} from "../../services/api/agentProfile";
import { ApiRequestError } from "../../services/api/fetch";
import type {
  AgentProfileAdminProjection,
  AgentProfileMutationResponse,
} from "../../types";
import {
  agentBuilderBlockReason,
  buildAgentProfileDraftRequest,
  createUnsavedAgentEditor,
  getAgentProfilePublishBlock,
  getAgentProfileSaveBlock,
  hasUnsavedAgentProfileEdits,
  hydrateAgentProfileEditor,
  isAgentProfileEditorDirty,
  type AgentBuilderCurrentCatalog,
  type AgentBuilderEditor,
} from "./agentBuilderAdapter";

export interface AgentBuilderProfileApi {
  listAdmin: () => Promise<{ agent_profiles: AgentProfileAdminProjection[] }>;
  saveDraft: (
    draft: ReturnType<typeof buildAgentProfileDraftRequest>,
    agentId?: string,
  ) => Promise<AgentProfileMutationResponse>;
  publish: (agentId: string, expectedRevision: number) => Promise<AgentProfileMutationResponse>;
  unpublish?: (agentId: string, expectedRevision: number) => Promise<AgentProfileMutationResponse>;
  runTest?: (
    agentId: string,
    expectedRevision: number,
    message: string,
    submissionId: string,
  ) => Promise<AgentProfileTrialRunResponse>;
}

export interface AgentBuilderSafeError {
  message: string;
  status?: number;
  code?: string;
}

export type AgentBuilderMutationState =
  | { phase: "idle" }
  | { phase: "saving" }
  | { phase: "publishing" }
  | { phase: "unpublishing" }
  | { phase: "testing" }
  | {
      phase: "success";
      action: "save" | "publish" | "unpublish" | "test";
      revision: number;
      trialRun?: AgentProfileTrialRunResponse;
    }
  | {
      phase: "error";
      action: "save" | "publish" | "unpublish" | "test";
      error: AgentBuilderSafeError;
    };

export interface AgentBuilderControllerState {
  listPhase: "idle" | "loading" | "ready" | "error";
  destructiveReloadPending: boolean;
  profiles: readonly AgentProfileAdminProjection[];
  activeEditor: AgentBuilderEditor | null;
  localEditor: AgentBuilderEditor | null;
  listError: AgentBuilderSafeError | null;
  mutation: AgentBuilderMutationState;
}

const SAFE_ERROR_CODE = /^[a-z][a-z0-9_]{0,63}$/;

function safeErrorCopy(
  action: "load" | "save" | "publish" | "unpublish" | "test",
  status?: number,
  code?: string,
) {
  if (code === "agent_profile_revision_stale" || code === "agent_profile_create_revision_invalid") {
    return "服务端 revision 已变化，请刷新列表后重新编辑。";
  }
  if (code === "agent_profile_revision_invalid" || code === "agent_id_invalid") {
    return "服务端拒绝了当前智能体版本标识，请刷新列表后重试。";
  }
  if (code === "agent_profile_capability_not_available") {
    return "所选 Skill 或 MCP 工具已不可用，请刷新目录后重新选择。";
  }
  if (code === "agent_profile_model_not_available") {
    return "所选模型已不可用，请刷新目录后重新选择。";
  }
  if (code === "not_ai_admin" || status === 403) {
    return "当前账号没有管理智能体的权限。";
  }
  if (status === 401) return "登录状态已失效，请重新登录后重试。";
  if (status === 409) return "服务端版本发生冲突，请刷新列表后重试。";
  if (status === 422) return "配置未通过服务端校验，请检查各项后重试。";
  if (action === "load") return "暂时无法加载服务端智能体列表，请稍后重试。";
  if (action === "save") return "暂时无法保存智能体草稿，请稍后重试。";
  if (action === "publish") return "暂时无法发布智能体草稿，请稍后重试。";
  if (action === "unpublish") return "暂时无法下架当前智能体，请稍后重试。";
  return "暂时无法创建受控测试运行，请稍后重试。";
}

/** Project only a typed HTTP status and bounded code; never surface raw detail. */
export function projectAgentBuilderError(
  action: "load" | "save" | "publish" | "unpublish" | "test",
  error: unknown,
): AgentBuilderSafeError {
  if (!(error instanceof ApiRequestError)) {
    return { message: safeErrorCopy(action) };
  }
  const status = Number.isInteger(error.status) && error.status >= 100 && error.status <= 599
    ? error.status
    : undefined;
  const code = typeof error.code === "string" && SAFE_ERROR_CODE.test(error.code)
    ? error.code
    : undefined;
  const detail = [status ? `HTTP ${status}` : null, code ? `代码 ${code}` : null]
    .filter(Boolean)
    .join("，");
  return {
    message: `${safeErrorCopy(action, status, code)}${detail ? `（${detail}）` : ""}`,
    ...(status ? { status } : {}),
    ...(code ? { code } : {}),
  };
}

function cloneProfile(profile: AgentProfileAdminProjection): AgentProfileAdminProjection {
  return {
    ...profile,
    selected_skill: { ...profile.selected_skill },
    mcp_tool_ids: [...profile.mcp_tool_ids],
    starter_prompts: [...profile.starter_prompts],
    recommended_tasks: [...profile.recommended_tasks],
    supported_input_types: [...profile.supported_input_types],
    supported_file_types: [...profile.supported_file_types],
    expected_outputs: [...profile.expected_outputs],
    allowed_department_ids: [...profile.allowed_department_ids],
    allowed_roles: [...profile.allowed_roles],
    allowed_user_ids: [...profile.allowed_user_ids],
  };
}

function upsertProfile(
  profiles: readonly AgentProfileAdminProjection[],
  profile: AgentProfileAdminProjection,
): AgentProfileAdminProjection[] {
  const next = cloneProfile(profile);
  const index = profiles.findIndex((entry) => entry.agent_id === next.agent_id);
  if (index < 0) return [next, ...profiles];
  return profiles.map((entry, entryIndex) => (entryIndex === index ? next : entry));
}

/**
 * Headless server-backed list/editor owner. It fences overlapping loads and
 * mutations while treating every API response as the sole revision authority.
 */
export class AgentBuilderController {
  private stateValue: AgentBuilderControllerState = {
    listPhase: "idle",
    destructiveReloadPending: false,
    profiles: [],
    activeEditor: null,
    localEditor: null,
    listError: null,
    mutation: { phase: "idle" },
  };

  private listener: ((state: AgentBuilderControllerState) => void) | null = null;
  private loadGeneration = 0;
  private mutationGeneration = 0;

  constructor(private readonly api: AgentBuilderProfileApi = agentProfileApi) {}

  get state(): AgentBuilderControllerState {
    return this.stateValue;
  }

  /** Subscribe one React owner to immutable controller snapshots. */
  subscribe(listener: (state: AgentBuilderControllerState) => void): () => void {
    this.listener = listener;
    listener(this.stateValue);
    return () => {
      if (this.listener === listener) this.listener = null;
    };
  }

  private commit(state: AgentBuilderControllerState): AgentBuilderControllerState {
    this.stateValue = state;
    this.listener?.(state);
    return state;
  }

  private hasActiveMutation(): boolean {
    return this.stateValue.mutation.phase === "saving" ||
      this.stateValue.mutation.phase === "publishing" ||
      this.stateValue.mutation.phase === "unpublishing" ||
      this.stateValue.mutation.phase === "testing";
  }

  /** Prevent pending asynchronous work from changing the current editor. */
  cancelPending(): void {
    this.loadGeneration += 1;
    this.mutationGeneration += 1;
  }

  /** Load or refresh latest same-tenant profiles and reopen the active identity. */
  async loadProfiles(discardUnsavedChanges = false): Promise<AgentBuilderControllerState> {
    if (this.hasActiveMutation() || this.stateValue.destructiveReloadPending) {
      return this.stateValue;
    }
    const generation = ++this.loadGeneration;
    this.commit({
      ...this.stateValue,
      listPhase: "loading",
      destructiveReloadPending: discardUnsavedChanges,
      listError: null,
      mutation: discardUnsavedChanges || this.stateValue.mutation.phase === "success"
        ? { phase: "idle" }
        : this.stateValue.mutation,
    });
    try {
      const response = await this.api.listAdmin();
      if (generation !== this.loadGeneration) return this.stateValue;
      const profiles = response.agent_profiles.map(cloneProfile);
      const current = this.stateValue.activeEditor;
      let activeEditor = current;
      if (current?.agentId) {
        const reopened = profiles.find((profile) => profile.agent_id === current.agentId);
        if (discardUnsavedChanges || !isAgentProfileEditorDirty(current)) {
          activeEditor = reopened
            ? hydrateAgentProfileEditor(reopened)
            : profiles[0]
              ? hydrateAgentProfileEditor(profiles[0])
              : null;
        }
      } else if (!current || discardUnsavedChanges) {
        activeEditor = profiles[0] ? hydrateAgentProfileEditor(profiles[0]) : null;
      }
      return this.commit({
        ...this.stateValue,
        listPhase: "ready",
        destructiveReloadPending: false,
        profiles,
        activeEditor,
        localEditor: discardUnsavedChanges ? null : this.stateValue.localEditor,
        listError: null,
      });
    } catch (error) {
      if (generation !== this.loadGeneration) return this.stateValue;
      return this.commit({
        ...this.stateValue,
        listPhase: "error",
        destructiveReloadPending: false,
        listError: projectAgentBuilderError("load", error),
      });
    }
  }

  /** Select one exact server profile from the current list. */
  selectProfile(
    agentId: string,
    discardUnsavedChanges = false,
  ): AgentBuilderControllerState {
    if (this.hasActiveMutation() || this.stateValue.destructiveReloadPending) {
      return this.stateValue;
    }
    const current = this.stateValue.activeEditor;
    if (current?.agentId === agentId) return this.stateValue;
    if (current && hasUnsavedAgentProfileEdits(current) && !discardUnsavedChanges) {
      return this.stateValue;
    }
    const profile = this.stateValue.profiles.find((entry) => entry.agent_id === agentId);
    if (!profile) return this.stateValue;
    this.mutationGeneration += 1;
    return this.commit({
      ...this.stateValue,
      activeEditor: hydrateAgentProfileEditor(profile),
      localEditor: current?.agentId === null && discardUnsavedChanges
        ? null
        : this.stateValue.localEditor,
      mutation: { phase: "idle" },
    });
  }

  /** Create or reopen the single unsaved local form. */
  createNewAgent(discardUnsavedChanges = false): AgentBuilderControllerState {
    if (this.hasActiveMutation() || this.stateValue.destructiveReloadPending) {
      return this.stateValue;
    }
    const current = this.stateValue.activeEditor;
    if (current?.agentId === null) return this.stateValue;
    if (current && hasUnsavedAgentProfileEdits(current) && !discardUnsavedChanges) {
      return this.stateValue;
    }
    this.mutationGeneration += 1;
    const localEditor = this.stateValue.localEditor ?? createUnsavedAgentEditor();
    return this.commit({
      ...this.stateValue,
      activeEditor: localEditor,
      localEditor,
      mutation: { phase: "idle" },
    });
  }

  /** Apply one local edit while retaining the materialized optimistic lock. */
  updateActiveEditor(
    update: (editor: AgentBuilderEditor) => AgentBuilderEditor,
  ): AgentBuilderControllerState {
    if (this.hasActiveMutation() || this.stateValue.destructiveReloadPending) {
      return this.stateValue;
    }
    const current = this.stateValue.activeEditor;
    if (!current) return this.stateValue;
    this.mutationGeneration += 1;
    const activeEditor = update(current);
    return this.commit({
      ...this.stateValue,
      activeEditor,
      localEditor: activeEditor.agentId ? this.stateValue.localEditor : activeEditor,
      mutation: { phase: "idle" },
    });
  }

  /** Save one valid editor and replace local identity/revision from the response. */
  async saveActiveProfile(
    catalog: AgentBuilderCurrentCatalog,
  ): Promise<AgentBuilderControllerState> {
    if (this.stateValue.destructiveReloadPending) return this.stateValue;
    const editor = this.stateValue.activeEditor;
    const block = getAgentProfileSaveBlock(editor, catalog);
    if (block || !editor) {
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "error",
          action: "save",
          error: { message: agentBuilderBlockReason(block ?? { code: "no_selection" }) },
        },
      });
    }
    if (this.hasActiveMutation()) {
      return this.stateValue;
    }
    const generation = ++this.mutationGeneration;
    this.loadGeneration += 1;
    this.commit({
      ...this.stateValue,
      listPhase: this.stateValue.listPhase === "loading"
        ? (this.stateValue.profiles.length > 0 ? "ready" : "idle")
        : this.stateValue.listPhase,
      destructiveReloadPending: false,
      mutation: { phase: "saving" },
    });
    try {
      const response = await this.api.saveDraft(
        buildAgentProfileDraftRequest(editor),
        editor.agentId ?? undefined,
      );
      if (generation !== this.mutationGeneration) return this.stateValue;
      const profile = cloneProfile(response.agent_profile);
      return this.commit({
        ...this.stateValue,
        listPhase: "ready",
        profiles: upsertProfile(this.stateValue.profiles, profile),
        activeEditor: hydrateAgentProfileEditor(profile),
        localEditor: editor.agentId ? this.stateValue.localEditor : null,
        mutation: { phase: "success", action: "save", revision: profile.revision },
      });
    } catch (error) {
      if (generation !== this.mutationGeneration) return this.stateValue;
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "error",
          action: "save",
          error: projectAgentBuilderError("save", error),
        },
      });
    }
  }

  /** Publish only one clean saved draft and adopt the returned revision/status. */
  async publishActiveProfile(
    catalog: AgentBuilderCurrentCatalog,
  ): Promise<AgentBuilderControllerState> {
    if (this.stateValue.destructiveReloadPending) return this.stateValue;
    const editor = this.stateValue.activeEditor;
    const block = getAgentProfilePublishBlock(editor, catalog);
    if (block || !editor?.agentId || !editor.revision) {
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "error",
          action: "publish",
          error: { message: agentBuilderBlockReason(block ?? { code: "save_required" }) },
        },
      });
    }
    if (this.hasActiveMutation()) {
      return this.stateValue;
    }
    const generation = ++this.mutationGeneration;
    this.loadGeneration += 1;
    this.commit({
      ...this.stateValue,
      listPhase: this.stateValue.listPhase === "loading"
        ? (this.stateValue.profiles.length > 0 ? "ready" : "idle")
        : this.stateValue.listPhase,
      destructiveReloadPending: false,
      mutation: { phase: "publishing" },
    });
    try {
      const response = await this.api.publish(editor.agentId, editor.revision);
      if (generation !== this.mutationGeneration) return this.stateValue;
      const profile = cloneProfile(response.agent_profile);
      return this.commit({
        ...this.stateValue,
        listPhase: "ready",
        profiles: upsertProfile(this.stateValue.profiles, profile),
        activeEditor: hydrateAgentProfileEditor(profile),
        mutation: { phase: "success", action: "publish", revision: profile.revision },
      });
    } catch (error) {
      if (generation !== this.mutationGeneration) return this.stateValue;
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "error",
          action: "publish",
          error: projectAgentBuilderError("publish", error),
        },
      });
    }
  }

  /** Withdraw one exact current publication while preserving immutable history. */
  async unpublishActiveProfile(): Promise<AgentBuilderControllerState> {
    const editor = this.stateValue.activeEditor;
    if (
      this.hasActiveMutation() ||
      !this.api.unpublish ||
      !editor?.agentId ||
      !editor.revision ||
      editor.status !== "published" ||
      isAgentProfileEditorDirty(editor)
    ) {
      return this.stateValue;
    }
    const generation = ++this.mutationGeneration;
    this.commit({ ...this.stateValue, mutation: { phase: "unpublishing" } });
    try {
      const response = await this.api.unpublish(editor.agentId, editor.revision);
      if (generation !== this.mutationGeneration) return this.stateValue;
      const profile = cloneProfile(response.agent_profile);
      return this.commit({
        ...this.stateValue,
        profiles: upsertProfile(this.stateValue.profiles, profile),
        activeEditor: hydrateAgentProfileEditor(profile),
        mutation: {
          phase: "success",
          action: "unpublish",
          revision: profile.revision,
        },
      });
    } catch (error) {
      if (generation !== this.mutationGeneration) return this.stateValue;
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "error",
          action: "unpublish",
          error: projectAgentBuilderError("unpublish", error),
        },
      });
    }
  }

  /** Execute a published revision through the real Agent App submission chain. */
  async runActiveProfileTest(message: string): Promise<AgentBuilderControllerState> {
    const editor = this.stateValue.activeEditor;
    if (
      this.hasActiveMutation() ||
      !this.api.runTest ||
      !editor?.agentId ||
      !editor.revision ||
      editor.status !== "published" ||
      isAgentProfileEditorDirty(editor) ||
      !message.trim()
    ) {
      return this.stateValue;
    }
    const generation = ++this.mutationGeneration;
    this.commit({ ...this.stateValue, mutation: { phase: "testing" } });
    try {
      const trialRun = await this.api.runTest(
        editor.agentId,
        editor.revision,
        message.trim(),
        crypto.randomUUID(),
      );
      if (generation !== this.mutationGeneration) return this.stateValue;
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "success",
          action: "test",
          revision: editor.revision,
          trialRun,
        },
      });
    } catch (error) {
      if (generation !== this.mutationGeneration) return this.stateValue;
      return this.commit({
        ...this.stateValue,
        mutation: {
          phase: "error",
          action: "test",
          error: projectAgentBuilderError("test", error),
        },
      });
    }
  }
}
