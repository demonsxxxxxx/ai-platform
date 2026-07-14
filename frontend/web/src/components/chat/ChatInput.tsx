import {
  useState,
  useRef,
  useEffect,
  useCallback,
  useMemo,
  useReducer,
  memo,
} from "react";
import toast from "react-hot-toast";
import { Ban } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ImageViewer } from "../common";
import { ConfirmDialog } from "../common/ConfirmDialog";
import { ContactAdminDialog } from "../common/ContactAdminDialog";
import { useFileUpload } from "../../hooks/useFileUpload";
import { useInputHistory } from "../../hooks/useInputHistory";
import { useTextareaResize } from "../../hooks/useTextareaResize";
import { usePasteHandler } from "../../hooks/usePasteHandler";
import { useAuth } from "../../hooks/useAuth";
import { ChatInputToolbar } from "./ChatInputToolbar";
import { ChatInputSelectors } from "./ChatInputSelectors";
import { ChatInputHelpMenu } from "./ChatInputHelpMenu";
import { ChatInputAttachments } from "./ChatInputAttachments";
import {
  parseComposerCommand,
  resolveComposerCommandDraft,
  resolveSlashCommandMenu,
  type ComposerCommandPanel,
  type SlashCommandMenuItem,
} from "./chatInputCommands";
import { ComposerChips } from "./ComposerChips";
import { SlashCommandMenu } from "./SlashCommandMenu";
import {
  composerSelectionReducer,
  type ComposerSelection,
  type ComposerSelectionKind,
} from "./composerSelections";
import { FILE_CATEGORY_PERMISSIONS } from "./chatInputConstants";
import {
  consumePendingSelectionActionPrompt,
  SELECTION_ACTION_EVENT,
  type SelectionActionEventDetail,
} from "../common/selectionActionPrompt";
import type { ChatInputProps } from "./chatInputTypes";
import type { FeaturePanel } from "../selectors/FeatureMenu";
import type {
  MessageAttachment,
  PublicSkillResponse,
} from "../../types";
import {
  prepareSelectedSkillSubmission,
} from "../../hooks/useSelectedSkillTask";
import {
  LibreChatComposerBox,
  LibreChatComposerFrame,
  LibreChatComposerRegion,
  LibreChatComposerTextarea,
} from "../../librechat-ui/Composer";

export type { ChatInputProps } from "./chatInputTypes";

export const ChatInput = memo(function ChatInput({
  draft: externalDraft,
  onDraftChange,
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
  selectedSkillState,
  onSelectSkill,
  onClearSelectedSkill,
  onSelectedSkillRecoverable,
  onSelectedSkillFilesReady,
  skillsLoading: _skillsLoading,
  enabledSkillsCount = 0,
  totalSkillsCount = 0,
  enableSkills = true,
  agentOptions,
  agentOptionValues = {},
  onToggleAgentOption,
  availableModels = [],
  currentModelId,
  onSelectModel,
  attachments: externalAttachments,
  onAttachmentsChange: externalOnAttachmentsChange,
  pendingInput,
  onPendingInputConsumed,
  className,
}: ChatInputProps) {
  const { t } = useTranslation();
  const [internalDraft, setInternalDraft] = useState("");
  const input = externalDraft ?? internalDraft;
  const setInput = onDraftChange ?? setInternalDraft;

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
  const [commandSearchSeed, setCommandSearchSeed] = useState<{
    panel: FeaturePanel;
    query: string;
  } | null>(null);
  const [slashMenuOpen, setSlashMenuOpen] = useState(false);
  const [slashMenuHighlight, setSlashMenuHighlight] = useState(0);
  const [internalAttachments, setInternalAttachments] = useState<
    MessageAttachment[]
  >([]);
  const [imageViewerSrc, setImageViewerSrc] = useState<string | null>(null);
  const [isDraggingOver, setIsDraggingOver] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [contactAdminOpen, setContactAdminOpen] = useState(false);
  const [composerSelections, dispatchComposerSelection] = useReducer(
    composerSelectionReducer,
    [],
  );

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const openFileCommandRef = useRef<(() => void) | null>(null);
  const isSubmittingRef = useRef(false);
  const [, setCursorPosition] = useState(0);
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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSend) return;
    if (handleComposerCommandSubmit(input)) return;
    if (input.trim() && !isLoading && !disabled && !isSubmittingRef.current) {
      const trimmed = input.trim();
      const selectedSkillSubmission = selectedSkillState
        ? prepareSelectedSkillSubmission(selectedSkillState, attachments)
        : { error: null, request: null };
      if (selectedSkillSubmission.error) {
        await onSelectedSkillRecoverable?.(selectedSkillSubmission.error);
        return;
      }

      isSubmittingRef.current = true;
      try {
        const outcome = await onSend(
          trimmed,
          agentOptionValues,
          attachments,
          selectedSkillSubmission.request,
        );
        if (outcome.status === "recoverable_error") {
          await onSelectedSkillRecoverable?.(outcome.code);
          return;
        }
        if (outcome.status === "accepted") {
          pushHistory(trimmed);
          setInput("");
          setAttachments([]);
          onClearSelectedSkill?.();
          requestAnimationFrame(() => {
            if (textareaRef.current) textareaRef.current.style.height = "auto";
          });
        }
      } finally {
        isSubmittingRef.current = false;
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (slashMenuOpen) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSlashMenuHighlight((index) =>
          slashCommandItems.length
            ? (index - 1 + slashCommandItems.length) % slashCommandItems.length
            : 0,
        );
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSlashMenuHighlight((index) =>
          slashCommandItems.length
            ? (index + 1) % slashCommandItems.length
            : 0,
        );
        return;
      }
      if (e.key === "Enter" || e.key === "Tab") {
        e.preventDefault();
        const item =
          slashCommandItems[slashMenuHighlight] ?? slashCommandItems[0];
        if (item) handleSlashCommandSelect(item);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        closeSlashMenu();
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
  const skillsAvailable =
    enableSkills && !!onSelectSkill;
  const toolsAvailable = !!onToggleTool && !!onToggleCategory && !!onToggleAll;
  const commandPanelAvailability = useMemo(
    () => ({
      skills: skillsAvailable,
      tools: toolsAvailable,
      models: !!availableModels?.length && !!onSelectModel,
      files: uploadCategories.length > 0,
      context: true,
    }),
    [
      availableModels?.length,
      onSelectModel,
      skillsAvailable,
      toolsAvailable,
      uploadCategories.length,
    ],
  );
  const canSubmit =
    hasContent && canSend && !isLoading && !hasUploadingAttachment;

  const upsertUnavailableCommandChip = useCallback(
    (command: ReturnType<typeof parseComposerCommand>) => {
      if (!command) return;
      if (command.panel === "command-menu") return;
      const selectionKindByPanel: Record<
        Exclude<ComposerCommandPanel, "command-menu">,
        ComposerSelectionKind
      > = {
        skills: "skill",
        tools: "mcp",
        thinking: "context",
        model: "model",
        file: "file",
        context: "context",
      };
      const kind = selectionKindByPanel[command.panel];
      const label = command.query
        ? `${command.command}: ${command.query}`
        : `/${command.command}`;
      dispatchComposerSelection({
        type: "upsert",
        selection: {
          id: `unavailable:${command.command}`,
          kind,
          label,
          state: "unavailable",
        description: t(
          "composerChip.unavailableDescription",
          "This command is visible in the composer, but your current workspace cannot use it yet.",
        ),
      },
    });
    },
    [t],
  );

  const closeSlashMenu = useCallback(() => {
    setSlashMenuOpen(false);
    setSlashMenuHighlight(0);
  }, []);

  const executeAvailableFileCommand = useCallback(() => {
    openFileCommandRef.current?.();
    setActivePanel(null);
    setCommandSearchSeed(null);
    closeSlashMenu();
    setInput("");
    setCursorPosition(0);
    requestAnimationFrame(scheduleTextareaResize);
  }, [closeSlashMenu, scheduleTextareaResize]);

  const upsertContextUnavailableChip = useCallback(() => {
    dispatchComposerSelection({
      type: "upsert",
      selection: {
        id: "unavailable:context-selector",
        kind: "context",
        label: t("composerCommand.contextSelector.chip", "/context"),
        state: "unavailable",
        source: "context-selector",
        description: t(
          "composerCommand.contextSelector.description",
          "Context selection is visible in the composer, but your workspace cannot use saved context yet.",
        ),
      },
    });
  }, [t]);

  const markContextUnavailableCommand = useCallback(() => {
    upsertContextUnavailableChip();
    setInput("");
    setCursorPosition(0);
    setActivePanel(null);
    setCommandSearchSeed(null);
    closeSlashMenu();
    requestAnimationFrame(scheduleTextareaResize);
  }, [
    closeSlashMenu,
    scheduleTextareaResize,
    upsertContextUnavailableChip,
  ]);

  const openCommandPanel = useCallback(
    (nextValue: string): boolean => {
      const draft = resolveComposerCommandDraft(
        nextValue,
        commandPanelAvailability,
      );
      if (!draft) return false;
      if (draft.command.unavailable) {
        upsertUnavailableCommandChip(draft.command);
        setActivePanel(null);
        setCommandSearchSeed(null);
        closeSlashMenu();
        setInput("");
        setCursorPosition(0);
        requestAnimationFrame(scheduleTextareaResize);
        return true;
      }
      if (draft.panel === "command-menu") {
        setSlashMenuOpen(true);
        setSlashMenuHighlight(0);
        setActivePanel(null);
        setCommandSearchSeed(null);
        return true;
      }
      if (draft.panel) {
        setActivePanel(draft.panel);
        setCommandSearchSeed({
          panel: draft.panel,
          query: draft.selectorQuery,
        });
        setSlashMenuOpen(false);
      }
      return true;
    },
    [
      closeSlashMenu,
      commandPanelAvailability,
      scheduleTextareaResize,
      upsertUnavailableCommandChip,
    ],
  );

  const slashCommandItems = useMemo(
    () =>
      slashMenuOpen
        ? resolveSlashCommandMenu(input, commandPanelAvailability)
        : [],
    [commandPanelAvailability, input, slashMenuOpen],
  );

  useEffect(() => {
    if (slashMenuHighlight >= slashCommandItems.length) {
      setSlashMenuHighlight(Math.max(0, slashCommandItems.length - 1));
    }
  }, [slashCommandItems.length, slashMenuHighlight]);

  const handleSlashCommandSelect = useCallback(
    (item: SlashCommandMenuItem) => {
      const nextInput = `/${item.command}${input.trimStart().slice(1).trim() ? " " : ""}`;
      closeSlashMenu();
      if (item.command === "file" && !item.unavailable) {
        executeAvailableFileCommand();
        return;
      }
      if (item.unavailable) {
        upsertUnavailableCommandChip({
          trigger: "/",
          command: item.command,
          panel: item.panel,
          query: "",
          unavailable: true,
        });
        setActivePanel(null);
        setCommandSearchSeed(null);
        setInput("");
        setCursorPosition(0);
        requestAnimationFrame(scheduleTextareaResize);
        return;
      }
      if (item.panel === "context") {
        markContextUnavailableCommand();
        return;
      }
      setInput(nextInput);
      setCursorPosition(nextInput.length);
      setActivePanel(item.panel);
      setCommandSearchSeed({
        panel: item.panel,
        query: "",
      });
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) return;
        textarea.focus();
        textarea.selectionStart = textarea.selectionEnd = nextInput.length;
        scheduleTextareaResize();
      });
    },
    [
      closeSlashMenu,
      executeAvailableFileCommand,
      input,
      markContextUnavailableCommand,
      scheduleTextareaResize,
      upsertUnavailableCommandChip,
    ],
  );

  const handleComposerCommandSubmit = useCallback(
    (value: string): boolean => {
      const draft = resolveComposerCommandDraft(value, commandPanelAvailability);
      if (!draft) return false;
      if (draft.panel === "command-menu") {
        const item = slashCommandItems[slashMenuHighlight] ?? slashCommandItems[0];
        if (item) {
          handleSlashCommandSelect(item);
        }
        return true;
      }
      if (!draft.shouldExecute) {
        if (draft.panel) {
          setActivePanel(draft.panel);
          setCommandSearchSeed({
            panel: draft.panel,
            query: draft.selectorQuery,
          });
          if (draft.panel === "context") {
            markContextUnavailableCommand();
            return true;
          }
        }
        return true;
      }
      if (
        draft.command.command === "file" &&
        !draft.command.unavailable &&
        !draft.command.query
      ) {
        executeAvailableFileCommand();
        return true;
      }
      upsertUnavailableCommandChip(
        draft.command.unavailable
          ? draft.command
          : { ...draft.command, unavailable: true },
      );
      setActivePanel(null);
      setCommandSearchSeed(null);
      closeSlashMenu();
      setInput("");
      setCursorPosition(0);
      requestAnimationFrame(scheduleTextareaResize);
      return true;
    },
    [
      commandPanelAvailability,
      closeSlashMenu,
      executeAvailableFileCommand,
      handleSlashCommandSelect,
      markContextUnavailableCommand,
      scheduleTextareaResize,
      slashCommandItems,
      slashMenuHighlight,
      upsertUnavailableCommandChip,
    ],
  );

  const handlePanelChange = useCallback(
    (panel: FeaturePanel) => {
      setCommandSearchSeed(null);
      if (panel === null) {
        setActivePanel(null);
        closeSlashMenu();
        requestAnimationFrame(() => textareaRef.current?.focus());
        return;
      }
      if (panel === "file") {
        openFileCommandRef.current?.();
        return;
      }
      if (panel === "context") {
        markContextUnavailableCommand();
        return;
      }
      setActivePanel(panel);
      closeSlashMenu();
    },
    [closeSlashMenu, markContextUnavailableCommand],
  );

  const handleSelectTaskSkill = useCallback(
    (skill: PublicSkillResponse) => {
      onSelectSkill?.(skill);
      const draft = resolveComposerCommandDraft(
        input,
        commandPanelAvailability,
      );
      if (draft?.panel === "skills") {
        setInput("");
        setCursorPosition(0);
        requestAnimationFrame(scheduleTextareaResize);
      }
      setActivePanel(null);
      setCommandSearchSeed(null);
      closeSlashMenu();
    },
    [
      closeSlashMenu,
      commandPanelAvailability,
      input,
      onSelectSkill,
      scheduleTextareaResize,
    ],
  );

  useEffect(() => {
    const fileSelections = attachments.map<ComposerSelection>((attachment) => ({
      id: `file:${attachment.id}`,
      kind: "file",
      label: attachment.name,
      state: attachment.isUploading ? "pending" : "enabled",
      referenceId: attachment.id,
      description: t("chat.fileReferenceChip", {
        name: attachment.name,
        type: t(`fileUpload.categories.${attachment.type}`),
      }),
    }));

    dispatchComposerSelection({ type: "clear-kind", kind: "file" });
    for (const selection of fileSelections) {
      dispatchComposerSelection({ type: "upsert", selection });
    }
  }, [attachments, t]);

  useEffect(() => {
    dispatchComposerSelection({ type: "clear-kind", kind: "skill" });
    const selectedSkill = selectedSkillState?.selectedSkill;
    if (!selectedSkill) return;

    const state =
      selectedSkillState.status === "stale"
        ? "unavailable"
        : selectedSkillState.status === "file_required"
          ? "pending"
          : "enabled";
    const fileRequirement = selectedSkill.requires_file
      ? t("skillSelector.fileRequired", "File required")
      : t("skillSelector.noFileRequired", "No file required");
    dispatchComposerSelection({
      type: "upsert",
      selection: {
        id: `skill:${selectedSkill.name}`,
        kind: "skill",
        label: selectedSkill.name,
        state,
        referenceId: selectedSkill.expected_version.slice(0, 8),
        description: `v${selectedSkill.expected_version.slice(0, 8)} · ${fileRequirement}`,
        visibleDetails: [
          `v${selectedSkill.expected_version.slice(0, 8)}`,
          fileRequirement,
        ],
      },
    });
  }, [selectedSkillState, t]);

  useEffect(() => {
    if (
      selectedSkillState?.status === "file_required" &&
      attachments.some((attachment) => attachment.id && !attachment.isUploading)
    ) {
      onSelectedSkillFilesReady?.();
    }
  }, [attachments, onSelectedSkillFilesReady, selectedSkillState?.status]);

  useEffect(() => {
    dispatchComposerSelection({ type: "clear-kind", kind: "mcp" });
  }, [tools]);

  useEffect(() => {
    dispatchComposerSelection({ type: "clear-kind", kind: "model" });
  }, [currentModelId]);

  const handleSelectModelChip = useCallback(
    (modelId: string, modelValue: string) => {
      onSelectModel?.(modelId, modelValue);
      dispatchComposerSelection({ type: "remove", id: `unavailable:model` });
      setInput("");
      setCursorPosition(0);
      setActivePanel(null);
      setCommandSearchSeed(null);
      closeSlashMenu();
      requestAnimationFrame(scheduleTextareaResize);
    },
    [closeSlashMenu, onSelectModel, scheduleTextareaResize],
  );

  const handleRemoveComposerSelection = useCallback(
    (id: string) => {
      dispatchComposerSelection({ type: "remove", id });
      if (id.startsWith("file:")) {
        const attachmentId = id.slice("file:".length);
        setAttachments((previous) =>
          previous.filter((attachment) => attachment.id !== attachmentId),
        );
        return;
      }
      if (id.startsWith("skill:")) {
        onClearSelectedSkill?.();
        return;
      }
      if (id.startsWith("mcp:")) {
        const toolName = id.slice("mcp:".length);
        const tool = tools.find((item) => item.name === toolName);
        if (tool?.enabled) onToggleTool?.(toolName);
        return;
      }
      if (id.startsWith("model:")) {
        return;
      }
    },
    [
      onClearSelectedSkill,
      onToggleTool,
      setAttachments,
      tools,
    ],
  );

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
    <LibreChatComposerFrame>
      <form
        onSubmit={handleSubmit}
        className={
          className ?? "mx-auto max-w-3xl lg:max-w-4xl xl:max-w-5xl px-2"
        }
      >
        <div
          className="relative"
          data-composer-command-menu-anchor
        >
          {slashMenuOpen && (
            <SlashCommandMenu
              items={slashCommandItems}
              highlightedIndex={slashMenuHighlight}
              onHighlight={setSlashMenuHighlight}
              onSelect={handleSlashCommandSelect}
              onClose={closeSlashMenu}
            />
          )}
          <LibreChatComposerBox
            ref={containerRef}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            dragging={isDraggingOver}
          >
            <ChatInputAttachments
              attachments={attachments}
              onAttachmentsChange={setAttachments}
              onCancelUpload={cancelUpload}
              onImageViewerOpen={(url) => setImageViewerSrc(url)}
            />

            <LibreChatComposerRegion region="chips">
              <ComposerChips
                selections={composerSelections}
                onRemove={handleRemoveComposerSelection}
              />
            </LibreChatComposerRegion>

            {selectedSkillState?.recoveryCode && (
              <div
                className="mx-3 mt-2 rounded-lg border border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] px-3 py-2 text-xs leading-relaxed text-[var(--theme-warning)]"
                role="status"
                data-selected-skill-error={selectedSkillState.recoveryCode}
              >
                {selectedSkillState.recoveryCode === "skill_selection_stale"
                  ? t(
                      "skillSelector.staleSelection",
                      "This Skill version changed. Open Skills and confirm the current version before submitting again.",
                    )
                  : selectedSkillState.recoveryCode ===
                      "capability_not_authorized"
                    ? t(
                        "skillSelector.selectionDenied",
                        "The selected Skill is no longer available. Choose an authorized Skill again.",
                      )
                    : t(
                        "skillSelector.fileRequiredInline",
                        "Attach the required file before submitting this task.",
                      )}
              </div>
            )}

            <LibreChatComposerRegion region="textarea">
              <div className="relative">
                <LibreChatComposerTextarea
                  ref={textareaRef}
                  value={input}
                onChange={(e) => {
                  const nextValue = e.target.value;
                  setInput(nextValue);
                  setCursorPosition(e.target.selectionStart);
                  if (!openCommandPanel(nextValue)) {
                    closeSlashMenu();
                  }
                }}
                onFocus={scheduleTextareaResize}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={
                  canSend ? t("chat.placeholder") : t("chat.noPermission")
                }
                disabled={disabled || !canSend}
                rows={1}
                />
              </div>
            </LibreChatComposerRegion>

            <LibreChatComposerRegion region="toolbar">
              <ChatInputToolbar
                activePanel={activePanel}
                onActivePanelChange={handlePanelChange}
                canSend={canSend}
                isLoading={isLoading}
                canSubmit={canSubmit}
                hasUploadingAttachment={hasUploadingAttachment}
                enabledToolsCount={enabledToolsCount}
                totalToolsCount={totalToolsCount}
                enabledSkillsCount={enabledSkillsCount}
                totalSkillsCount={totalSkillsCount}
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
                onFileCommandReady={(openFileCommand) => {
                  openFileCommandRef.current = openFileCommand;
                }}
                onStopClick={() => setStopConfirmOpen(true)}
                onNoPermissionClick={() => setContactAdminOpen(true)}
              />
            </LibreChatComposerRegion>
          </LibreChatComposerBox>
        </div>
      </form>

      <ChatInputSelectors
        activePanel={activePanel}
        onActivePanelChange={handlePanelChange}
        commandSearchSeed={commandSearchSeed}
        tools={tools}
        onToggleTool={onToggleTool}
        onToggleCategory={onToggleCategory}
        onToggleAll={onToggleAll}
        enabledToolsCount={enabledToolsCount}
        totalToolsCount={totalToolsCount}
        skills={skills}
        selectedSkill={selectedSkillState?.selectedSkill}
        onSelectSkill={handleSelectTaskSkill}
        skillsLoading={_skillsLoading}
        enableSkills={enableSkills}
        availableModels={availableModels}
        currentModelId={currentModelId}
        onSelectModel={handleSelectModelChip}
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
    </LibreChatComposerFrame>
  );
});
