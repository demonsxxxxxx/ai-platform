import { useState, useRef, useEffect, useCallback, useMemo, memo } from "react";
import toast from "react-hot-toast";
import { Ban } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ImageViewer } from "../common";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ContactAdminDialog } from "../common/ContactAdminDialog";
import { useFileUpload } from "../../hooks/useFileUpload";
import { useMentionState } from "../../hooks/useMentionState";
import { useMentionSearch } from "../../hooks/useMentionSearch";
import { useInputHistory } from "../../hooks/useInputHistory";
import { useTextareaResize } from "../../hooks/useTextareaResize";
import { usePasteHandler } from "../../hooks/usePasteHandler";
import { useAuth } from "../../hooks/useAuth";
import { MentionPopup } from "./MentionPopup";
import { ChatInputToolbar } from "./ChatInputToolbar";
import { ChatInputSelectors } from "./ChatInputSelectors";
import { ChatInputHelpMenu } from "./ChatInputHelpMenu";
import { ChatInputAttachments } from "./ChatInputAttachments";
import { ComposerSelectionChips } from "./ComposerSelectionChips";
import { SlashCommandMenu } from "./SlashCommandMenu";
import { getMentionPopupFixedPlacement } from "./chatInputViewport";
import { FILE_CATEGORY_PERMISSIONS } from "./chatInputConstants";
import {
  applySlashCommandSelection,
  buildSlashCommandOptions,
  dedupeComposerTokens,
  findSlashCommandMatch,
  moveSlashCommandHighlight,
  type ComposerSelectionToken,
  type SlashCommandOption,
} from "./slashCommand";
import {
  consumePendingSelectionActionPrompt,
  SELECTION_ACTION_EVENT,
  type SelectionActionEventDetail,
} from "../common/selectionActionPrompt";
import type { ChatInputProps } from "./chatInputTypes";
import type { FeaturePanel } from "../selectors/FeatureMenu";
import type { MessageAttachment, PersonaPreset } from "../../types";

export type { ChatInputProps } from "./chatInputTypes";

export const ChatInput = memo(function ChatInput({
  onSend,
  onStop,
  isLoading,
  disabled,
  canSend = true,
  tools = [],
  onToggleTool,
  onToggleCategory,
  onToggleAll,
  toolsLoading: _toolsLoading,
  enabledToolsCount = 0,
  totalToolsCount = 0,
  skills = [],
  onToggleSkill,
  onToggleSkillCategory,
  onToggleAllSkills,
  skillsLoading: _skillsLoading,
  pendingSkillNames = [],
  skillsMutating = false,
  enabledSkillsCount = 0,
  totalSkillsCount = 0,
  enableSkills = true,
  personaPresets = [],
  personaPresetsTotal,
  personaPresetsPage,
  onPersonaPresetsPageChange,
  onPersonaPresetsSearchChange,
  onPersonaPresetsTagChange,
  selectedPersonaPresetId,
  selectedPersonaName,
  personaSkillsControlled = false,
  personaPresetsLoading = false,
  personaPresetsMutating = false,
  onUsePersonaPreset,
  onCopyPersonaPreset,
  onClearPersonaPreset,
  canManagePersonaPresets = false,
  agentOptions,
  agentOptionValues = {},
  onToggleAgentOption,
  agents = [],
  currentAgent,
  onSelectAgent,
  attachments: externalAttachments,
  onAttachmentsChange: externalOnAttachmentsChange,
  onMentionQueryChange,
  pendingInput,
  onPendingInputConsumed,
  className,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [input, setInput] = useState("");

  // Consume external pendingInput: fill textarea and focus
  useEffect(() => {
    if (pendingInput) {
      setInput(pendingInput);
      onPendingInputConsumed?.();
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (textarea) {
          textarea.focus();
          textarea.selectionStart = textarea.selectionEnd = pendingInput.length;
        }
      });
    }
  }, [pendingInput, onPendingInputConsumed]);

  const [activePanel, setActivePanel] = useState<FeaturePanel>(null);
  const [internalAttachments, setInternalAttachments] = useState<
    MessageAttachment[]
  >([]);
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [contactAdminOpen, setContactAdminOpen] = useState(false);
  const [slashHighlightedIndex, setSlashHighlightedIndex] = useState(0);
  const [composerTokens, setComposerTokens] = useState<
    ComposerSelectionToken[]
  >([]);
  const [dismissedSlashKey, setDismissedSlashKey] = useState<string | null>(
    null,
  );

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [cursorPosition, setCursorPosition] = useState(0);
  const [mentionPopupPlacement, setMentionPopupPlacement] =
    useState<ReturnType<typeof getMentionPopupFixedPlacement>>(null);
  const [slashPopupPlacement, setSlashPopupPlacement] =
    useState<ReturnType<typeof getMentionPopupFixedPlacement>>(null);
  const { hasPermission } = useAuth();

  const uploadCategories = (
    Object.keys(FILE_CATEGORY_PERMISSIONS) as Array<
      keyof typeof FILE_CATEGORY_PERMISSIONS
    >
  ).filter((cat) => hasPermission(FILE_CATEGORY_PERMISSIONS[cat]));

  const attachments = externalAttachments ?? internalAttachments;
  const setAttachments = externalOnAttachmentsChange ?? setInternalAttachments;

  const { uploadFiles, uploadLimits, validateCount, cancelUpload } =
    useFileUpload({
      attachments,
      onAttachmentsChange: setAttachments,
    });

  const { history, pushHistory, navigateUp, navigateDown } = useInputHistory();

  const { scheduleTextareaResize } = useTextareaResize(textareaRef, input);

  const { handlePaste } = usePasteHandler({
    textareaRef,
    input,
    setInput,
    uploadFiles,
    validateCount,
    scheduleTextareaResize,
  });

  const {
    mention,
    moveHighlight: moveMentionHighlight,
    setHighlightedIndex: setMentionHighlight,
    setResultCount: setMentionResultCount,
    resetMention,
    dismissMention,
  } = useMentionState(input, cursorPosition, !!onUsePersonaPreset);

  const mentionSearch = useMentionSearch(mention.query, mention.isActive);

  useEffect(() => {
    if (mention.isActive) {
      setMentionResultCount(mentionSearch.presets.length);
    }
  }, [mention.isActive, mentionSearch.presets.length, setMentionResultCount]);

  useEffect(() => {
    if (!onMentionQueryChange) return;
    onMentionQueryChange(mention.isActive ? mention.query : null);
  }, [mention.isActive, mention.query, onMentionQueryChange]);

  useEffect(() => {
    if (!onMentionQueryChange || !selectedPersonaPresetId || !mention.isActive)
      return;
    const before = input.substring(0, mention.atIndex);
    const after = input.substring(mention.atIndex + mention.query.length + 1);
    setInput(before + after);
    setCursorPosition(before.length || 0);
    requestAnimationFrame(() => {
      const textarea = textareaRef.current;
      if (textarea) {
        textarea.selectionStart = textarea.selectionEnd = before.length;
        textarea.focus();
        scheduleTextareaResize();
      }
    });
    resetMention();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- fires only on preset selection
  }, [selectedPersonaPresetId]);

  useEffect(() => {
    const applySelectionActionPrompt = (prompt: string) => {
      setInput((previous) => {
        const next = previous.trim()
          ? `${previous.trim()}\n\n${prompt}`
          : prompt;
        setCursorPosition(next.length);
        requestAnimationFrame(() => {
          const textarea = textareaRef.current;
          if (!textarea) return;
          textarea.focus();
          textarea.selectionStart = textarea.selectionEnd = next.length;
          scheduleTextareaResize();
        });
        return next;
      });
    };

    const pendingPrompt = consumePendingSelectionActionPrompt();
    if (pendingPrompt) {
      applySelectionActionPrompt(pendingPrompt);
    }

    const handleSelectionAction = (event: Event) => {
      const detail = (event as CustomEvent<SelectionActionEventDetail>).detail;
      if (!detail?.prompt) return;
      applySelectionActionPrompt(detail.prompt);
    };

    window.addEventListener(SELECTION_ACTION_EVENT, handleSelectionAction);
    return () => {
      window.removeEventListener(SELECTION_ACTION_EVENT, handleSelectionAction);
    };
  }, [scheduleTextareaResize]);

  useEffect(() => {
    if (!mention.isActive) {
      setMentionPopupPlacement(null);
      return;
    }

    const updateMentionPopupPlacement = () => {
      const container = containerRef.current;
      setMentionPopupPlacement(
        getMentionPopupFixedPlacement({
          inputRect: container?.getBoundingClientRect() ?? null,
          viewportHeight: window.visualViewport?.height ?? window.innerHeight,
        }),
      );
    };

    updateMentionPopupPlacement();
    window.addEventListener("resize", updateMentionPopupPlacement);
    window.addEventListener("scroll", updateMentionPopupPlacement, true);
    window.visualViewport?.addEventListener(
      "resize",
      updateMentionPopupPlacement,
    );
    window.visualViewport?.addEventListener(
      "scroll",
      updateMentionPopupPlacement,
    );
    return () => {
      window.removeEventListener("resize", updateMentionPopupPlacement);
      window.removeEventListener("scroll", updateMentionPopupPlacement, true);
      window.visualViewport?.removeEventListener(
        "resize",
        updateMentionPopupPlacement,
      );
      window.visualViewport?.removeEventListener(
        "scroll",
        updateMentionPopupPlacement,
      );
    };
  }, [mention.isActive]);

  const slashMatch = useMemo(
    () => findSlashCommandMatch(input, cursorPosition),
    [input, cursorPosition],
  );
  const slashMatchKey = slashMatch
    ? `${slashMatch.slashIndex}:${slashMatch.query}`
    : null;

  const slashOptions = useMemo(
    () =>
      slashMatch
        ? buildSlashCommandOptions({
            query: slashMatch.query,
            skills,
            tools,
            agents,
            currentAgent,
            agentOptions,
            agentOptionValues,
            uploadCategories,
          })
        : [],
    [
      slashMatch,
      skills,
      tools,
      agents,
      currentAgent,
      agentOptions,
      agentOptionValues,
      uploadCategories,
    ],
  );

  const slashMenuActive =
    !!slashMatch &&
    slashMatchKey !== dismissedSlashKey &&
    slashOptions.length > 0;

  useEffect(() => {
    if (!slashMenuActive) {
      setSlashPopupPlacement(null);
      setSlashHighlightedIndex(0);
      return;
    }

    setSlashHighlightedIndex((current) =>
      slashOptions.length === 0 ? 0 : Math.min(current, slashOptions.length - 1),
    );

    const updateSlashPopupPlacement = () => {
      const container = containerRef.current;
      setSlashPopupPlacement(
        getMentionPopupFixedPlacement({
          inputRect: container?.getBoundingClientRect() ?? null,
          viewportHeight: window.visualViewport?.height ?? window.innerHeight,
        }),
      );
    };

    updateSlashPopupPlacement();
    window.addEventListener("resize", updateSlashPopupPlacement);
    window.addEventListener("scroll", updateSlashPopupPlacement, true);
    window.visualViewport?.addEventListener("resize", updateSlashPopupPlacement);
    window.visualViewport?.addEventListener("scroll", updateSlashPopupPlacement);
    return () => {
      window.removeEventListener("resize", updateSlashPopupPlacement);
      window.removeEventListener("scroll", updateSlashPopupPlacement, true);
      window.visualViewport?.removeEventListener(
        "resize",
        updateSlashPopupPlacement,
      );
      window.visualViewport?.removeEventListener(
        "scroll",
        updateSlashPopupPlacement,
      );
    };
  }, [slashMenuActive, slashOptions.length]);

  const personaAvatar = useMemo(() => {
    if (!selectedPersonaPresetId) return null;
    const preset = personaPresets.find((p) => p.id === selectedPersonaPresetId);
    if (!preset) return null;
    return {
      avatar: preset.avatar ?? undefined,
      primaryTag: preset.tags[0] || "",
    };
  }, [selectedPersonaPresetId, personaPresets]);

  const applyMentionSelection = useCallback(
    (preset: PersonaPreset) => {
      if (!mention.isActive) return;
      const before = input.substring(0, mention.atIndex);
      const after = input.substring(mention.atIndex + mention.query.length + 1);
      const newInput = before + after;
      setInput(newInput);
      setCursorPosition(before.length || 0);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (textarea) {
          textarea.selectionStart = textarea.selectionEnd = before.length;
          textarea.focus();
          scheduleTextareaResize();
        }
      });
      onUsePersonaPreset?.(preset);
      resetMention();
    },
    [input, mention, onUsePersonaPreset, resetMention, scheduleTextareaResize],
  );

  const applySlashSelection = useCallback(
    async (option: SlashCommandOption) => {
      if (!slashMatch || option.disabled) return;
      const result = applySlashCommandSelection(input, slashMatch, option);

      if (option.group === "skill" && option.value) {
        const skillName = String(option.value);
        const skill = skills.find((candidate) => candidate.name === skillName);
        if (skill && !skill.enabled) {
          const toggled = await onToggleSkill?.(skillName);
          if (!toggled) return;
        }
      }

      if (option.group === "mcp" && option.value) {
        const toolName = String(option.value);
        const tool = tools.find((candidate) => candidate.name === toolName);
        if (tool?.system_disabled) return;
        if (tool && !tool.enabled) {
          onToggleTool?.(toolName);
        }
      }

      if (option.group === "agent" && option.value) {
        onSelectAgent?.(String(option.value));
      }

      if (
        option.group === "model" &&
        option.optionKey &&
        option.value !== undefined
      ) {
        onToggleAgentOption?.(option.optionKey, option.value);
      }

      if (result.nextPanel) {
        setActivePanel(result.nextPanel);
      }

      setInput(result.input);
      setCursorPosition(result.cursorPosition);
      if (result.token) {
        setComposerTokens((current) =>
          dedupeComposerTokens(current, result.token!),
        );
      }
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.selectionStart = textarea.selectionEnd = result.cursorPosition;
        textarea.focus();
        scheduleTextareaResize();
      });
    },
    [
      input,
      slashMatch,
      skills,
      onToggleSkill,
      tools,
      onToggleTool,
      onSelectAgent,
      onToggleAgentOption,
      scheduleTextareaResize,
    ],
  );

  const removeComposerToken = useCallback((token: ComposerSelectionToken) => {
    setComposerTokens((current) =>
      current.filter(
        (candidate) =>
          candidate.type !== token.type || candidate.id !== token.id,
      ),
    );
  }, []);

  const buildSubmitOptions = useCallback(() => {
    const selectedSkillNames = composerTokens
      .filter((token) => token.type === "skill" && token.state === "selected")
      .map((token) => token.id);
    const selectedMcpTools = composerTokens
      .filter((token) => token.type === "mcp" && token.state === "selected")
      .map((token) => token.id);

    return {
      ...agentOptionValues,
      ...(selectedSkillNames.length > 0
        ? { selected_skill_names: selectedSkillNames.join(",") }
        : {}),
      ...(selectedMcpTools.length > 0
        ? { selected_mcp_tools: selectedMcpTools.join(",") }
        : {}),
    };
  }, [agentOptionValues, composerTokens]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSend) return;
    if (input.trim() && !isLoading && !disabled) {
      const trimmed = input.trim();
      onSend(trimmed, buildSubmitOptions(), attachments);
      pushHistory(trimmed);
      setInput("");
      setComposerTokens([]);
      setAttachments([]);
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.style.height = "auto";
        }
      });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (slashMenuActive) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashHighlightedIndex((current) =>
          moveSlashCommandHighlight(current, "up", slashOptions.length),
        );
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashHighlightedIndex((current) =>
          moveSlashCommandHighlight(current, "down", slashOptions.length),
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const highlighted = slashOptions[slashHighlightedIndex];
        if (highlighted) void applySlashSelection(highlighted);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setSlashHighlightedIndex(0);
        setSlashPopupPlacement(null);
        setDismissedSlashKey(slashMatchKey);
        return;
      }
    }

    if (mention.isActive) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        moveMentionHighlight("up");
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        moveMentionHighlight("down");
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const highlighted = mentionSearch.presets[mention.highlightedIndex];
        if (highlighted) applyMentionSelection(highlighted);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        resetMention();
        return;
      }
    }

    const newlineModifier = localStorage.getItem("newlineModifier") || "shift";

    if (e.key === "Enter") {
      const needsModifier = newlineModifier === "ctrl" ? e.ctrlKey : e.shiftKey;
      if (needsModifier) return;

      e.preventDefault();
      if (isLoading) {
        setStopConfirmOpen(true);
      } else {
        handleSubmit(e);
      }
      return;
    }

    const textarea = textareaRef.current;
    const atTop =
      textarea?.selectionStart === 0 && textarea?.selectionEnd === 0;
    const value = textarea?.value ?? "";
    const atBottom =
      textarea?.selectionStart === value.length &&
      textarea?.selectionEnd === value.length;

    if (e.key === "ArrowUp" && atTop) {
      e.preventDefault();
      const prev = navigateUp(input);
      if (prev !== null) {
        setInput(prev);
        requestAnimationFrame(() => {
          if (textarea) {
            textarea.selectionStart = textarea.selectionEnd = prev.length;
          }
        });
      }
    } else if (e.key === "ArrowDown" && (atBottom || history.length > 0)) {
      e.preventDefault();
      const next = navigateDown();
      if (next !== null) {
        setInput(next);
        requestAnimationFrame(() => {
          if (textarea) {
            textarea.selectionStart = textarea.selectionEnd =
              textarea.value.length;
          }
        });
      }
    }
  };

  const hasContent = !!input.trim() && !disabled;
  const hasUploadingAttachment = attachments.some((a) => a.isUploading);
  const canSubmit =
    hasContent && canSend && !isLoading && !hasUploadingAttachment;

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    if (!validateCount(files.length)) return;
    uploadFiles(files);
  };

  const thinkingLabel = agentOptions
    ? Object.entries(agentOptions)
        .filter(([, opt]) => opt.options && opt.options.length > 0)
        .map(([, opt]) => {
          const val =
            agentOptionValues[
              Object.keys(agentOptions).find((k) => agentOptions[k] === opt)!
            ] ?? opt.default;
          const selected = opt.options?.find((o) => o.value === val);
          return selected?.label_key
            ? t(selected.label_key)
            : selected?.label || String(val);
        })[0]
    : undefined;

  const thinkingLevel = agentOptions
    ? Object.entries(agentOptions)
        .filter(([, opt]) => opt.options && opt.options.length > 0)
        .map(([, opt]) => {
          const val =
            agentOptionValues[
              Object.keys(agentOptions).find((k) => agentOptions[k] === opt)!
            ] ?? opt.default;
          return String(val);
        })[0]
    : undefined;

  return (
    <div
      className="chat-input-shell sm:px-4 pb-3"
      style={{ backgroundColor: "var(--theme-bg)" }}
    >
      <form
        onSubmit={handleSubmit}
        className={
          className ?? "mx-auto max-w-3xl lg:max-w-4xl xl:max-w-5xl px-2"
        }
      >
        <div
          ref={containerRef}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          className={`chat-input-container flex flex-col relative w-full rounded-3xl px-1 border transition-all duration-300 ${
            isDraggingOver ? "border-dashed shadow-lg border-2" : ""
          }`}
          data-mention-active={mention.isActive || undefined}
          data-slash-active={slashMenuActive || undefined}
          style={{
            backgroundColor: "var(--theme-bg-card)",
            borderColor: isDraggingOver
              ? "var(--theme-primary)"
              : "var(--theme-border)",
            boxShadow: isDraggingOver
              ? undefined
              : "0 2px 12px rgba(0,0,0,0.06)",
          }}
        >
          {mention.isActive && !onMentionQueryChange && (
            <MentionPopup
              presets={mentionSearch.presets}
              highlightedIndex={mention.highlightedIndex}
              selectedPresetId={selectedPersonaPresetId}
              isLoading={mentionSearch.isLoading}
              isLoadingMore={mentionSearch.isLoadingMore}
              hasMore={mentionSearch.hasMore}
              onSelect={applyMentionSelection}
              onHover={setMentionHighlight}
              onClose={dismissMention}
              onLoadMore={mentionSearch.loadMore}
              placement={mentionPopupPlacement ?? undefined}
            />
          )}

          {slashMenuActive && (
            <SlashCommandMenu
              options={slashOptions}
              highlightedIndex={slashHighlightedIndex}
              onSelect={(option) => void applySlashSelection(option)}
              onHover={setSlashHighlightedIndex}
              onClose={() => {
                setSlashHighlightedIndex(0);
                setSlashPopupPlacement(null);
                setDismissedSlashKey(slashMatchKey);
              }}
              placement={slashPopupPlacement ?? undefined}
            />
          )}

          <ChatInputAttachments
            attachments={attachments}
            onAttachmentsChange={setAttachments}
            onCancelUpload={cancelUpload}
            onImageViewerOpen={(url) => setImageViewerSrc(url)}
          />

          <ComposerSelectionChips
            tokens={composerTokens}
            onRemove={removeComposerToken}
          />

          <div className="px-2.5 pt-1">
            <div className="relative">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setCursorPosition(e.target.selectionStart);
                }}
                onFocus={scheduleTextareaResize}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={
                  canSend ? t("chat.placeholder") : t("chat.noPermission")
                }
                disabled={disabled || !canSend}
                className="bg-transparent outline-none w-full pt-[10px] resize-none text-[15px] disabled:opacity-50 leading-relaxed overflow-y-auto [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none] min-h-[40px] sm:min-h-[44px]"
                style={{
                  color: "var(--theme-text)",
                  paddingLeft: 4,
                }}
                rows={1}
              />
            </div>
          </div>

          <ChatInputToolbar
            activePanel={activePanel}
            onActivePanelChange={setActivePanel}
            canSend={canSend}
            isLoading={isLoading}
            canSubmit={canSubmit}
            hasUploadingAttachment={hasUploadingAttachment}
            enabledToolsCount={enabledToolsCount}
            totalToolsCount={totalToolsCount}
            enabledSkillsCount={enabledSkillsCount}
            totalSkillsCount={totalSkillsCount}
            hasPersonaSelector={!!onUsePersonaPreset}
            personaName={selectedPersonaName}
            hasAgentSelector={agents.length > 1 && !!onSelectAgent}
            agentName={agents.find((a) => a.id === currentAgent)?.name}
            hasThinkingOption={
              !!(
                agentOptions &&
                onToggleAgentOption &&
                Object.keys(agentOptions).length > 0
              )
            }
            thinkingLabel={thinkingLabel}
            thinkingLevel={thinkingLevel}
            uploadCategories={uploadCategories}
            uploadLimits={uploadLimits}
            uploadFiles={uploadFiles}
            selectedPersonaName={selectedPersonaName}
            personaAvatar={personaAvatar}
            onClearPersonaPreset={onClearPersonaPreset}
            onStopClick={() => setStopConfirmOpen(true)}
            onNoPermissionClick={() => setContactAdminOpen(true)}
          />
        </div>
      </form>

      <ChatInputSelectors
        activePanel={activePanel}
        onActivePanelChange={setActivePanel}
        tools={tools}
        onToggleTool={onToggleTool}
        onToggleCategory={onToggleCategory}
        onToggleAll={onToggleAll}
        enabledToolsCount={enabledToolsCount}
        totalToolsCount={totalToolsCount}
        skills={skills}
        onToggleSkill={onToggleSkill}
        onToggleSkillCategory={onToggleSkillCategory}
        onToggleAllSkills={onToggleAllSkills}
        pendingSkillNames={pendingSkillNames}
        skillsMutating={skillsMutating}
        enabledSkillsCount={enabledSkillsCount}
        totalSkillsCount={totalSkillsCount}
        enableSkills={enableSkills}
        personaSkillsControlled={personaSkillsControlled}
        selectedPersonaName={selectedPersonaName}
        personaPresets={personaPresets}
        personaPresetsTotal={personaPresetsTotal}
        personaPresetsPage={personaPresetsPage}
        onPersonaPresetsPageChange={onPersonaPresetsPageChange}
        onPersonaPresetsSearchChange={onPersonaPresetsSearchChange}
        onPersonaPresetsTagChange={onPersonaPresetsTagChange}
        selectedPersonaPresetId={selectedPersonaPresetId}
        personaPresetsLoading={personaPresetsLoading}
        personaPresetsMutating={personaPresetsMutating}
        onUsePersonaPreset={onUsePersonaPreset}
        onCopyPersonaPreset={onCopyPersonaPreset}
        onClearPersonaPreset={onClearPersonaPreset}
        canManagePersonaPresets={canManagePersonaPresets}
        agents={agents}
        currentAgent={currentAgent}
        onSelectAgent={onSelectAgent}
        agentOptions={agentOptions}
        agentOptionValues={agentOptionValues}
        onToggleAgentOption={onToggleAgentOption}
      />

      <ChatInputHelpMenu />

      {imageViewerSrc && (
        <ImageViewer
          src={imageViewerSrc}
          isOpen={!!imageViewerSrc}
          onClose={() => setImageViewerSrc(null)}
        />
      )}

      <ConfirmDialog
        isOpen={stopConfirmOpen}
        title={t("chat.stopConfirmTitle")}
        message={t("chat.stopConfirmMessage")}
        confirmText={t("chat.stop")}
        cancelText={t("common.cancel")}
        variant="warning"
        onConfirm={() => {
          setStopConfirmOpen(false);
          onStop();
          toast.custom(() => (
            <div
              className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
              style={{
                background:
                  "color-mix(in srgb, var(--theme-primary) 10%, transparent)",
                border:
                  "1px solid color-mix(in srgb, var(--theme-primary) 20%, transparent)",
                color: "var(--theme-primary)",
              }}
            >
              <Ban size={16} className="shrink-0" />
              <span>{t("chat.status.cancelled")}</span>
            </div>
          ));
        }}
        onCancel={() => setStopConfirmOpen(false)}
      />

      <ContactAdminDialog
        isOpen={contactAdminOpen}
        onClose={() => setContactAdminOpen(false)}
        reason="noPermission"
      />
    </div>
  );
});
