import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronRight,
  CircleAlert,
  FileWarning,
  Plus,
  RefreshCw,
  Save,
  Settings2,
  Wrench,
} from "lucide-react";

import type { ModelOption } from "../../services/api/modelPublic";
import { agentProfileApi } from "../../services/api/agentProfile";
import type {
  AgentProfileAdminProjection,
  AgentProfilePublicProjection,
  PublicSkillResponse,
} from "../../types";
import { useAgent } from "../../hooks/useAgent";
import { AgentBuilderDialog } from "./AgentBuilderDialog";
import {
  type AgentBuilderCurrentCatalog,
  type AgentBuilderDraft,
  type AgentBuilderSafeMcpTool,
} from "./agentBuilderAdapter";
import {
  AgentBuilderController,
  type AgentBuilderChatIdentity,
  type AgentBuilderChatSubmitSeam,
  type AgentBuilderControllerState,
} from "./agentBuilderController";

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

interface LocalDraft {
  id: string;
  name: string;
  draft: AgentBuilderDraft;
}

export interface AgentBuilderWorkbenchProps {
  catalog: AgentBuilderWorkbenchCatalog;
  canManageProfiles?: boolean;
  onHandoffReady?: (path: string, identity: AgentBuilderChatIdentity) => void;
}

interface AgentBuilderWorkbenchHarnessProps extends AgentBuilderWorkbenchProps {
  chat: AgentBuilderChatSubmitSeam;
  chatIdentity: AgentBuilderChatIdentity | null;
}

function createLocalDraft(id: string, name: string): LocalDraft {
  return {
    id,
    name,
    draft: {
      message: "",
      description: "",
      instructions: "",
      model: null,
      selectedSkill: null,
      selectedMcpToolIds: [],
      selectedAgentProfile: null,
    },
  };
}

function updateDraft(
  drafts: readonly LocalDraft[],
  draftId: string,
  update: (draft: AgentBuilderDraft) => AgentBuilderDraft,
): LocalDraft[] {
  return drafts.map((entry) =>
    entry.id === draftId ? { ...entry, draft: update(entry.draft) } : entry,
  );
}

function controllerMessage(state: AgentBuilderControllerState): string | null {
  if (state.phase === "blocked" && state.code === "file_attachment_unavailable") {
    return "This Skill requires a file. File upload is unavailable in Agent Builder.";
  }
  if (state.phase === "blocked" && state.code === "catalog_unavailable") {
    return "The authorized catalog is refreshing. Wait before submitting.";
  }
  if (state.phase === "blocked" && state.code === "selected_skill_stale") {
    return "The selected Skill changed or is no longer authorized. Choose it again.";
  }
  if (state.phase === "blocked" && state.code === "selected_mcp_tool_unavailable") {
    return "One or more selected MCP tools changed or are no longer authorized. Choose the current tools again.";
  }
  if (state.phase === "blocked" && state.code === "selected_model_stale") {
    return "The selected model changed or is no longer available. Choose it again.";
  }
  if (state.phase === "blocked") return "Enter a message before submitting.";
  if (state.phase === "error") return "Chat submission was not accepted. Update the draft and retry.";
  if (state.phase === "awaiting_chat_identity") return "Opening the authoritative Chat run...";
  return null;
}

interface AgentBuilderWorkbenchState {
  controllerState: AgentBuilderControllerState;
  drafts: LocalDraft[];
  activeDraft: LocalDraft;
  dialog: "instructions" | "skills" | "tools" | null;
  setDialog: (dialog: "instructions" | "skills" | "tools" | null) => void;
  replaceActiveDraft: (update: (current: AgentBuilderDraft) => AgentBuilderDraft) => void;
  renameActiveDraft: (name: string) => void;
  switchDraft: (draftId: string) => void;
  createDraft: () => void;
  submit: (chat: AgentBuilderChatSubmitSeam) => Promise<void>;
  acceptChatIdentity: (identity: AgentBuilderChatIdentity | null) => void;
  markProfileDraft: (profile: AgentProfileAdminProjection) => void;
  markProfilePublished: (profile: AgentProfileAdminProjection) => void;
  selectPublishedProfile: (profile: AgentProfilePublicProjection) => void;
}

function useAgentBuilderWorkbenchState(
  catalog: AgentBuilderWorkbenchCatalog,
): AgentBuilderWorkbenchState {
  const controllerRef = useRef(new AgentBuilderController());
  const [controllerState, setControllerState] = useState<AgentBuilderControllerState>(
    controllerRef.current.state,
  );
  const [drafts, setDrafts] = useState<LocalDraft[]>(() => [
    createLocalDraft("local-draft-1", "New agent"),
    createLocalDraft("local-draft-2", "Research agent"),
  ]);
  const [activeDraftId, setActiveDraftId] = useState("local-draft-1");
  const nextDraftIdRef = useRef(3);
  const [dialog, setDialog] = useState<"instructions" | "skills" | "tools" | null>(
    null,
  );

  const activeDraft = useMemo(
    () => drafts.find((entry) => entry.id === activeDraftId) ?? drafts[0]!,
    [activeDraftId, drafts],
  );
  const draft = activeDraft.draft;
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

  const invalidateDraftMutation = useCallback(() => {
    if (controllerRef.current.state.phase !== "ready") {
      setControllerState(controllerRef.current.invalidateDraft());
    }
  }, []);

  const replaceActiveDraft = useCallback((update: (current: AgentBuilderDraft) => AgentBuilderDraft) => {
    invalidateDraftMutation();
    setDrafts((current) => updateDraft(current, activeDraft.id, (draft) => ({
      ...update(draft),
      selectedAgentProfile: null,
    })));
  }, [activeDraft.id, invalidateDraftMutation]);

  const renameActiveDraft = useCallback((name: string) => {
    invalidateDraftMutation();
    setDrafts((current) => current.map((entry) => (
      entry.id === activeDraft.id ? { ...entry, name } : entry
    )));
  }, [activeDraft.id, invalidateDraftMutation]);

  const switchDraft = useCallback((draftId: string) => {
    if (draftId === activeDraftId) return;
    setControllerState(controllerRef.current.invalidateDraft());
    setActiveDraftId(draftId);
  }, [activeDraftId]);

  const createDraft = useCallback(() => {
    const id = `local-draft-${nextDraftIdRef.current++}`;
    setControllerState(controllerRef.current.invalidateDraft());
    setDrafts((current) => [...current, createLocalDraft(id, "New agent")]);
    setActiveDraftId(id);
  }, []);

  const submit = useCallback(async (chat: AgentBuilderChatSubmitSeam) => {
    const submittedDraftId = activeDraft.id;
    const next = await controllerRef.current.submit(draft, currentCatalog, chat);
    setControllerState(next);
    if (next.phase === "blocked") {
      setDrafts((current) =>
        updateDraft(current, submittedDraftId, () => next.sanitizedDraft),
      );
    }
  }, [activeDraft.id, currentCatalog, draft]);

  const acceptChatIdentity = useCallback((identity: AgentBuilderChatIdentity | null) => {
    setControllerState(controllerRef.current.acceptChatIdentity(identity));
  }, []);

  const markProfileDraft = useCallback((profile: AgentProfileAdminProjection) => {
    setDrafts((current) => updateDraft(current, activeDraft.id, (draft) => ({
      ...draft,
      agentId: profile.agent_id,
      draftRevision: profile.revision,
      selectedAgentProfile: null,
    })));
  }, [activeDraft.id]);

  const markProfilePublished = useCallback((profile: AgentProfileAdminProjection) => {
    setDrafts((current) => updateDraft(current, activeDraft.id, (draft) => ({
      ...draft,
      agentId: profile.agent_id,
      draftRevision: profile.revision,
      selectedAgentProfile: {
        agent_id: profile.agent_id,
        expected_revision: profile.revision,
      },
    })));
  }, [activeDraft.id]);

  const selectPublishedProfile = useCallback((profile: AgentProfilePublicProjection) => {
    setControllerState(controllerRef.current.invalidateDraft());
    setDrafts((current) => updateDraft(current, activeDraft.id, (draft) => ({
      ...draft,
      selectedAgentProfile: {
        agent_id: profile.agent_id,
        expected_revision: profile.expected_revision,
      },
    })));
  }, [activeDraft.id]);

  return {
    controllerState,
    drafts,
    activeDraft,
    dialog,
    setDialog,
    replaceActiveDraft,
    renameActiveDraft,
    switchDraft,
    createDraft,
    submit,
    acceptChatIdentity,
    markProfileDraft,
    markProfilePublished,
    selectPublishedProfile,
  };
}

function AgentBuilderWorkbenchContent({
  catalog,
  canManageProfiles = false,
  chat,
  chatIdentity,
  onHandoffReady,
  workbench,
}: AgentBuilderWorkbenchHarnessProps & { workbench: AgentBuilderWorkbenchState }) {
  const {
    controllerState,
    drafts,
    activeDraft,
    dialog,
    setDialog,
    replaceActiveDraft,
    renameActiveDraft,
    switchDraft,
    createDraft,
    submit,
    acceptChatIdentity,
    markProfileDraft,
    markProfilePublished,
    selectPublishedProfile,
  } = workbench;
  const draft = activeDraft.draft;
  const [persistenceState, setPersistenceState] = useState<{
    busy: boolean;
    error: string | null;
  }>({ busy: false, error: null });
  const [marketState, setMarketState] = useState<{
    profiles: AgentProfilePublicProjection[];
    loading: boolean;
    error: string | null;
  }>({ profiles: [], loading: true, error: null });
  const refreshPublishedProfiles = useCallback(async () => {
    setMarketState((current) => ({ ...current, loading: true, error: null }));
    try {
      const response = await agentProfileApi.listPublished();
      setMarketState({
        profiles: response.agent_profiles,
        loading: false,
        error: null,
      });
    } catch (error) {
      setMarketState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : "Unable to load published Agents.",
      }));
    }
  }, []);
  const canPersist = Boolean(
    activeDraft.name.trim() &&
    draft.instructions.trim() &&
    draft.model?.id &&
    draft.selectedSkill,
  );
  const saveDraft = useCallback(async () => {
    if (!canManageProfiles) return;
    if (!canPersist || !draft.model || !draft.selectedSkill) {
      setPersistenceState({ busy: false, error: "Choose a name, instructions, model, and Skill before saving." });
      return;
    }
    setPersistenceState({ busy: true, error: null });
    try {
      const response = await agentProfileApi.saveDraft(
        {
          name: activeDraft.name.trim(),
          description: (draft.description ?? "").trim(),
          instructions: draft.instructions,
          model_id: draft.model.id,
          selected_skill: {
            skill_id: draft.selectedSkill.name,
            expected_version: draft.selectedSkill.expected_version,
          },
          mcp_tool_ids: draft.selectedMcpToolIds,
          expected_draft_revision: draft.draftRevision ?? 0,
        },
        draft.agentId,
      );
      markProfileDraft(response.agent_profile);
      setPersistenceState({ busy: false, error: null });
    } catch (error) {
      setPersistenceState({
        busy: false,
        error: error instanceof Error ? error.message : "Unable to save the Agent draft.",
      });
    }
  }, [activeDraft.name, canManageProfiles, canPersist, draft, markProfileDraft]);
  const publishDraft = useCallback(async () => {
    if (!canManageProfiles) return;
    if (!draft.agentId || !draft.draftRevision) return;
    setPersistenceState({ busy: true, error: null });
    try {
      const response = await agentProfileApi.publish(draft.agentId, draft.draftRevision);
      markProfilePublished(response.agent_profile);
      setPersistenceState({ busy: false, error: null });
    } catch (error) {
      setPersistenceState({
        busy: false,
        error: error instanceof Error ? error.message : "Unable to publish the Agent draft.",
      });
    }
  }, [canManageProfiles, draft.agentId, draft.draftRevision, markProfilePublished]);
  const stateMessage =
    controllerMessage(controllerState) ??
    (draft.selectedSkill?.requires_file
      ? "This Skill requires a file. File upload is unavailable in Agent Builder."
      : null);
  const selectedModelIsCurrent =
    draft.model !== null &&
    catalog.models.some(
      (model) => model.id === draft.model?.id && model.value === draft.model.value,
    );

  useEffect(() => {
    if (controllerState.phase !== "awaiting_chat_identity") return;
    acceptChatIdentity(chatIdentity);
  }, [acceptChatIdentity, chatIdentity, controllerState.phase]);

  useEffect(() => {
    void refreshPublishedProfiles();
  }, [refreshPublishedProfiles]);

  useEffect(() => {
    if (controllerState.phase === "handoff_ready") {
      onHandoffReady?.(controllerState.path, controllerState.identity);
    }
  }, [controllerState, onHandoffReady]);

  const toggleTool = (toolId: string) => {
    replaceActiveDraft((current) => ({
      ...current,
      selectedMcpToolIds: current.selectedMcpToolIds.some(
        (id) => !catalog.tools.some((tool) => tool.id === id),
      )
        ? [toolId]
        : current.selectedMcpToolIds.includes(toolId)
          ? current.selectedMcpToolIds.filter((id) => id !== toolId)
          : [...current.selectedMcpToolIds, toolId],
    }));
  };

  const submitDisabled =
    catalog.isLoading ||
    !draft.message.trim() ||
    (!draft.selectedAgentProfile && draft.selectedSkill?.requires_file === true) ||
    controllerState.phase === "submitting" ||
    controllerState.phase === "awaiting_chat_identity";

  return (
    <main data-agent-builder-workbench className="flex min-h-0 flex-1 flex-col overflow-hidden bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]">
      <header className="flex shrink-0 items-center justify-between border-b border-[var(--theme-border)] px-4 py-3 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Bot size={20} className="shrink-0 text-[var(--theme-primary)]" aria-hidden="true" />
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold">Agent Builder</h1>
            <p className="text-sm text-[var(--theme-text-secondary)]">
              {draft.selectedAgentProfile
                ? `Published revision ${draft.selectedAgentProfile.expected_revision}`
                : draft.draftRevision
                  ? `Saved draft revision ${draft.draftRevision}`
                  : "Unsaved local draft"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            aria-label="Refresh catalogs"
            className="btn-secondary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
            disabled={catalog.isLoading}
            onClick={catalog.retry}
            type="button"
          >
            <RefreshCw
              size={16}
              className={catalog.isLoading ? "animate-spin" : undefined}
              aria-hidden="true"
            />
            <span className="hidden sm:inline">Refresh catalogs</span>
          </button>
          {canManageProfiles ? (
            <>
              <button
                className="btn-secondary hidden items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60 sm:inline-flex"
                disabled={persistenceState.busy || !canPersist}
                onClick={() => void saveDraft()}
                type="button"
              >
                <Save size={16} aria-hidden="true" />
                Save draft
              </button>
              <button
                className="btn-primary hidden items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60 sm:inline-flex"
                disabled={persistenceState.busy || !draft.agentId || !draft.draftRevision}
                onClick={() => void publishDraft()}
                type="button"
              >
                Publish
              </button>
            </>
          ) : null}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[15rem_minmax(0,1fr)_18rem] lg:overflow-hidden">
        <aside className="border-b border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] lg:overflow-y-auto lg:border-b-0 lg:border-r">
          <div className="flex items-center justify-between px-4 py-3">
            <h2 className="text-sm font-semibold">Agents</h2>
            <button
              aria-label="Create local draft"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-[var(--theme-text-secondary)] hover:bg-[var(--theme-workbench-canvas)] hover:text-[var(--theme-text)]"
              onClick={createDraft}
              title="Create local draft"
              type="button"
            >
              <Plus size={17} aria-hidden="true" />
            </button>
          </div>
          <div className="border-t border-[var(--theme-border)]">
            {drafts.map((entry) => (
              <button
                key={entry.id}
                className={`flex w-full items-center gap-2 border-b border-[var(--theme-border)] px-4 py-3 text-left text-sm ${entry.id === activeDraft.id ? "bg-[var(--theme-workbench-canvas)] text-[var(--theme-text)]" : "text-[var(--theme-text-secondary)] hover:bg-[var(--theme-workbench-canvas)]"}`}
                onClick={() => switchDraft(entry.id)}
                type="button"
              >
                <Bot size={16} aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate">{entry.name}</span>
                {entry.id === activeDraft.id ? <ChevronRight size={15} aria-hidden="true" /> : null}
              </button>
            ))}
          </div>
        </aside>

        <section className="min-w-0 px-4 py-5 sm:px-6 lg:overflow-y-auto">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6">
            {catalog.error ? (
              <div className="flex items-center justify-between gap-3 border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200">
                <span className="min-w-0">{catalog.error}</span>
                <button className="btn-secondary shrink-0" onClick={catalog.retry} type="button">
                  Retry
                </button>
              </div>
            ) : null}
            {stateMessage ? (
              <div className="flex items-start gap-2 border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-900/70 dark:bg-amber-950/40 dark:text-amber-100">
                {controllerState.phase === "blocked" ? <FileWarning size={17} aria-hidden="true" /> : <CircleAlert size={17} aria-hidden="true" />}
                <span>{stateMessage}</span>
              </div>
            ) : null}
            {persistenceState.error ? (
              <div className="border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900/70 dark:bg-red-950/40 dark:text-red-200">
                {persistenceState.error}
              </div>
            ) : null}

            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">Name</span>
              <input
                className="h-10 w-full rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm outline-none focus:border-[var(--theme-primary)]"
                onChange={(event) => renameActiveDraft(event.target.value)}
                value={activeDraft.name}
              />
            </label>

            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">Description</span>
              <textarea
                aria-label="Agent description"
                className="min-h-20 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)]"
                onChange={(event) => replaceActiveDraft((current) => ({ ...current, description: event.target.value }))}
                placeholder="Safe market description"
                value={draft.description ?? ""}
              />
            </label>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-2">
                <span className="text-sm font-medium">Model</span>
                <select
                  className="h-10 rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 text-sm outline-none focus:border-[var(--theme-primary)]"
                  disabled={catalog.isLoading}
                  onChange={(event) => {
                    const next = catalog.models.find((item) => item.id === event.target.value) ?? null;
                    replaceActiveDraft((current) => ({ ...current, model: next }));
                  }}
                  value={selectedModelIsCurrent ? draft.model?.id : ""}
                >
                  <option value="">
                    {draft.model && !selectedModelIsCurrent
                      ? "Choose the current model again"
                      : "Default model"}
                  </option>
                  {catalog.models.map((item) => (
                    <option key={item.id} value={item.id}>{item.label}</option>
                  ))}
                </select>
              </label>
              <div className="flex flex-col gap-2">
                <span className="text-sm font-medium">Capabilities</span>
                <div className="flex h-10 items-center gap-2">
                  <button className="btn-secondary inline-flex flex-1 items-center justify-center gap-2" onClick={() => setDialog("skills")} type="button">
                    <Settings2 size={16} aria-hidden="true" />
                    {draft.selectedSkill?.name ?? "Select Skill"}
                  </button>
                  <button aria-label="Configure MCP tools" className="btn-secondary inline-flex h-10 w-10 items-center justify-center" onClick={() => setDialog("tools")} title="Configure MCP tools" type="button">
                    <Wrench size={16} aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-3">
                <span className="text-sm font-medium">Instructions</span>
                <button className="text-sm text-[var(--theme-primary)] hover:underline" onClick={() => setDialog("instructions")} type="button">
                  Edit
                </button>
              </div>
              <textarea
                aria-label="Local draft instructions"
                className="min-h-28 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)]"
                onChange={(event) => replaceActiveDraft((current) => ({ ...current, instructions: event.target.value }))}
                placeholder="Local draft instructions"
                value={draft.instructions}
              />
            </div>

            <label className="flex flex-col gap-2">
              <span className="text-sm font-medium">Preview message</span>
              <textarea
                aria-label="Preview message"
                className="min-h-32 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)]"
                onChange={(event) => replaceActiveDraft((current) => ({ ...current, message: event.target.value }))}
                placeholder="Write a message for the selected Chat run"
                value={draft.message}
              />
            </label>
            <div className="flex justify-end">
              <button className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-60" disabled={submitDisabled} onClick={() => void submit(chat)} type="button">
                {controllerState.phase === "submitting" ? <RefreshCw size={16} className="animate-spin" aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
                Open Chat run
              </button>
            </div>
          </div>
        </section>

        <aside className="border-t border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-4 py-5 lg:overflow-y-auto lg:border-l lg:border-t-0">
          <div className="border-b border-[var(--theme-border)] pb-4">
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-sm font-semibold">Published Agents</h2>
              <button
                className="text-xs text-[var(--theme-primary)] hover:underline"
                disabled={marketState.loading}
                onClick={() => void refreshPublishedProfiles()}
                type="button"
              >
                Refresh
              </button>
            </div>
            {marketState.error ? (
              <p className="mt-2 text-xs text-red-700 dark:text-red-300">{marketState.error}</p>
            ) : marketState.loading ? (
              <p className="mt-2 text-xs text-[var(--theme-text-secondary)]">Loading market…</p>
            ) : marketState.profiles.length === 0 ? (
              <p className="mt-2 text-xs text-[var(--theme-text-secondary)]">No published Agents are available.</p>
            ) : (
              <div className="mt-3 space-y-2">
                {marketState.profiles.map((profile) => {
                  const selected =
                    draft.selectedAgentProfile?.agent_id === profile.agent_id &&
                    draft.selectedAgentProfile.expected_revision === profile.expected_revision;
                  return (
                    <button
                      key={`${profile.agent_id}:${profile.expected_revision}`}
                      className={`w-full rounded-md border p-2 text-left text-xs transition-colors ${
                        selected
                          ? "border-[var(--theme-primary)] bg-[var(--theme-workbench-canvas)]"
                          : "border-[var(--theme-border)] hover:border-[var(--theme-border-strong)]"
                      }`}
                      onClick={() => selectPublishedProfile(profile)}
                      type="button"
                    >
                      <span className="block truncate font-medium">{profile.name}</span>
                      <span className="mt-1 block line-clamp-2 text-[var(--theme-text-secondary)]">
                        {profile.description || "Published Agent"}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
          <h2 className="mt-4 text-sm font-semibold">Chat handoff</h2>
          <dl className="mt-4 space-y-3 text-sm">
            <div>
              <dt className="text-[var(--theme-text-secondary)]">Skill</dt>
              <dd className="mt-1 break-words">{draft.selectedSkill?.name ?? "None"}</dd>
            </div>
            <div>
              <dt className="text-[var(--theme-text-secondary)]">MCP tools</dt>
              <dd className="mt-1">{draft.selectedMcpToolIds.length}</dd>
            </div>
            <div>
              <dt className="text-[var(--theme-text-secondary)]">Run</dt>
              <dd className="mt-1 break-all">{controllerState.phase === "handoff_ready" ? controllerState.identity.runId : "Not started"}</dd>
            </div>
          </dl>
        </aside>
      </div>

      <AgentBuilderDialog isOpen={dialog === "instructions"} onClose={() => setDialog(null)} title="Instructions">
        <textarea
          className="min-h-72 w-full resize-y rounded-md border border-[var(--theme-border)] bg-[var(--theme-workbench-canvas)] px-3 py-2 text-sm outline-none focus:border-[var(--theme-primary)]"
          onChange={(event) => replaceActiveDraft((current) => ({ ...current, instructions: event.target.value }))}
          value={draft.instructions}
        />
      </AgentBuilderDialog>
      <AgentBuilderDialog isOpen={dialog === "skills"} onClose={() => setDialog(null)} title="Skills">
        <div className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
          {catalog.skills.map((item) => {
            return (
              <button
                key={item.name}
                className="flex w-full items-start justify-between gap-4 px-1 py-3 text-left"
                onClick={() => {
                  replaceActiveDraft((current) => ({ ...current, selectedSkill: item }));
                  setDialog(null);
                }}
                type="button"
              >
                <span className="min-w-0"><span className="block font-medium">{item.name}</span><span className="mt-1 block text-sm text-[var(--theme-text-secondary)]">{item.description}</span></span>
                <span className="shrink-0 text-xs text-[var(--theme-text-secondary)]">{item.requires_file ? "File required" : item.expected_version}</span>
              </button>
            );
          })}
        </div>
      </AgentBuilderDialog>
      <AgentBuilderDialog isOpen={dialog === "tools"} onClose={() => setDialog(null)} title="MCP tools">
        <div className="divide-y divide-[var(--theme-border)] border-y border-[var(--theme-border)]">
          {catalog.tools.map((tool) => {
            const selected = draft.selectedMcpToolIds.includes(tool.id);
            return (
              <label key={tool.id} className="flex cursor-pointer items-start gap-3 px-1 py-3">
                <input checked={selected} onChange={() => toggleTool(tool.id)} type="checkbox" />
                <span className="min-w-0"><span className="block font-medium">{tool.label}</span><span className="mt-1 block text-sm text-[var(--theme-text-secondary)]">{tool.description}</span></span>
              </label>
            );
          })}
        </div>
      </AgentBuilderDialog>
    </main>
  );
}

/** Hidden reference-derived workbench with the real Chat submission seam. */
export function AgentBuilderWorkbench({
  catalog,
  canManageProfiles,
  onHandoffReady,
}: AgentBuilderWorkbenchProps) {
  const workbench = useAgentBuilderWorkbenchState(catalog);
  const preparedMcpToolIdsRef = useRef<readonly string[]>([]);
  const getDisabledMcpTools = useCallback(
    () => [...preparedMcpToolIdsRef.current],
    [],
  );
  const chat = useAgent(
    useMemo(() => ({ getDisabledMcpTools }), [getDisabledMcpTools]),
  );
  const builderChat = useMemo<AgentBuilderChatSubmitSeam>(
    () => ({
      sendMessage: (
        content,
        agentOptions,
        attachments,
        selectedSkill,
        selectedMcpToolIds = [],
        selectedAgentProfile,
      ) => {
        preparedMcpToolIdsRef.current = [...selectedMcpToolIds];
        return chat.sendMessage(
          content,
          agentOptions,
          attachments,
          selectedSkill,
          selectedAgentProfile,
        );
      },
    }),
    [chat.sendMessage],
  );
  const chatIdentity =
    chat.sessionId && chat.currentRunId
      ? { sessionId: chat.sessionId, runId: chat.currentRunId }
      : null;

  return (
    <AgentBuilderWorkbenchContent
      catalog={catalog}
      canManageProfiles={canManageProfiles}
      chat={builderChat}
      chatIdentity={chatIdentity}
      onHandoffReady={onHandoffReady}
      workbench={workbench}
    />
  );
}

/** Uses the production draft/controller state with an injected admission seam for UI tests. */
export function AgentBuilderWorkbenchHarness({
  catalog,
  canManageProfiles,
  chat,
  chatIdentity,
  onHandoffReady,
}: AgentBuilderWorkbenchHarnessProps) {
  const workbench = useAgentBuilderWorkbenchState(catalog);
  return (
    <AgentBuilderWorkbenchContent
      catalog={catalog}
      canManageProfiles={canManageProfiles}
      chat={chat}
      chatIdentity={chatIdentity}
      onHandoffReady={onHandoffReady}
      workbench={workbench}
    />
  );
}
