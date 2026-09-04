import {
  useState,
  useRef,
  useEffect,
  useLayoutEffect,
  useCallback,
  useMemo,
  useReducer,
  memo,
  type SetStateAction,
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
  reconcileChatInputSubmissionLock,
  releaseChatInputSubmissionLock,
  tryAcquireChatInputSubmissionLock,
} from "./chatInputSubmissionLock";
import {
  consumePendingSelectionActionPrompt,
  SELECTION_ACTION_EVENT,
  type SelectionActionEventDetail,
} from "../common/selectionActionPrompt";
import type {
  ChatInputDraftSnapshot,
  ChatInputProps,
} from "./chatInputTypes";
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

export type {
  ChatInputDraftSnapshot,
  ChatInputProps,
} from "./chatInputTypes";

export const ChatInput = memo(function ChatInput({
  initialDraft,
  initialDraftKey,
  draftSnapshotRef,
  draftScopeKey,
  draftScopeHandoffKey,
  onSend,
  onStop,
  isLoading,
  disabled,
  canSend = true,
  placeholder,
  acceptedFileTypes,
  disableSlashCommands = false,
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
  const localDraftSnapshotRef = useRef<ChatInputDraftSnapshot>({
    value: "",
    appliedInitialDraftKey: null,
    scopeKey: draftScopeKey,
    revision: 0,
    selectedSkillState,
    selectedSkillRevision: 0,
    pendingScopeHandoff: false,
  });
  const draftSnapshot =
    draftSnapshotRef?.current ?? localDraftSnapshotRef.current;
  const inputRef = useRef(draftSnapshot.value);
  const [input, setLocalInput] = useState(inputRef.current);
  if (draftSnapshot.selectedSkillState !== selectedSkillState) {
    draftSnapshot.selectedSkillState = selectedSkillState;
    draftSnapshot.selectedSkillRevision += 1;
  }

  useLayoutEffect(() => {
    const scopeChanged = draftSnapshot.scopeKey !== draftScopeKey;
    const preserveFirstSubmission =
      draftSnapshot.scopeKey == null &&
      draftScopeKey != null &&
      draftScopeKey === draftScopeHandoffKey &&
      draftSnapshot.pendingScopeHandoff;
    if (scopeChanged) {
      draftSnapshot.scopeKey = draftScopeKey;
      draftSnapshot.pendingScopeHandoff = false;
      if (!preserveFirstSubmission) {
        draftSnapshot.value = "";
        draftSnapshot.revision += 1;
        draftSnapshot.appliedInitialDraftKey = null;
      }
    }

    const apply = (value: string) => {
      inputRef.current = value;
      setLocalInput(value);
    };
    draftSnapshot.apply = apply;
    if (inputRef.current !== draftSnapshot.value) {
      apply(draftSnapshot.value);
    }
    return () => {
      if (draftSnapshot.apply === apply) draftSnapshot.apply = undefined;
    };
  }, [draftScopeHandoffKey, draftScopeKey, draftSnapshot]);

  const setInput = useCallback(
    (next: SetStateAction<string>) => {
      const value =
        typeof next === "function" ? next(draftSnapshot.value) : next;
      draftSnapshot.revision += 1;
      draftSnapshot.value = value;
      draftSnapshot.apply?.(value);
    },
    [draftSnapshot],
  );

  useEffect(() => {
    if (!initialDraft || !initialDraftKey) return;
    if (draftSnapshot.appliedInitialDraftKey === initialDraftKey) return;
    draftSnapshot.appliedInitialDraftKey = initialDraftKey;
    setInput((current) => current || initialDraft);
  }, [draftSnapshot, initialDraft, initialDraftKey, setInput]);

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
  }, [pendingInput, onPendingInputConsumed, setInput]);

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
  const [isStopSubmitting, setIsStopSubmitting] = useState(false);
  const [contactAdminOpen, setContactAdminOpen] = useState(false);
  const [composerSelections, dispatchComposerSelection] = useReducer(
    composerSelectionReducer,
    [],
  );

  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const openFileCommandRef = useRef<(() => void) | null>(null);
  const isSubmittingRef = useRef<symbol | null>(null);
  const { hasPermission } = useAuth();

  useEffect(() => {
    reconcileChatInputSubmissionLock(isSubmittingRef, isLoading);
  }, [isLoading]);

  const uploadCategories = (
    acceptedFileTypes?.length === 0
      ? []
      : (Object.keys(FILE_CATEGORY_PERMISSIONS) as Array<
          keyof typeof FILE_CATEGORY_PERMISSIONS
        >)
  ).filter((cat) => hasPermission(FILE_CATEGORY_PERMISSIONS[cat]));

  const attachments = externalAttachments ?? internalAttachments;
  const setAttachments = externalOnAttachmentsChange ?? setInternalAttachments;

  const { uploadFiles, uploadLimitsBytes, validateCount, cancelUpload } =
    useFileUpload({
      attachments,
      onAttachmentsChange: setAttachments,
      acceptedFileTypes,
    });

  const { history, pushHistory, navigateUp, navigateDown } = useInputHistory();

  const { scheduleTextareaResize } = useTextareaResize(textareaRef, input);

  const { handlePaste } = usePasteHandler({
    textareaRef,
    input,
    setInput,
    uploadFiles: acceptedFileTypes?.length === 0 ? () => {} : uploadFiles,
    validateCount,
    scheduleTextareaResize,
  });

  useEffect(() => {
    const applySelectionActionPrompt = (prompt: string) => {
      setInput((previous) => {
        const next = previous.trim()
          ? `${previous.trim()}\n\n${prompt}`
          : prompt;
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
  }, [scheduleTextareaResize, setInput]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSend) return;
    if (!disableSlashCommands && handleComposerCommandSubmit(input)) return;
    if (input.trim() && !isLoading && !disabled) {
      const trimmed = input.trim();
      const selectedSkillSubmission = selectedSkillState
        ? prepareSelectedSkillSubmission(selectedSkillState, attachments)
        : { error: null, request: null };
      if (selectedSkillSubmission.error) {
        await onSelectedSkillRecoverable?.(selectedSkillSubmission.error);
        return;
      }

      const submissionToken = tryAcquireChatInputSubmissionLock(isSubmittingRef);
      if (!submissionToken) return;
      const submittedRevision = draftSnapshot.revision;
      const submittedSkillRevision = draftSnapshot.selectedSkillRevision;
      const submittedAttachmentIds = new Set(
        attachments.map((attachment) => attachment.id),
      );
      if (draftSnapshot.scopeKey == null) {
        draftSnapshot.pendingScopeHandoff = true;
      }
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
          if (draftSnapshot.revision === submittedRevision) setInput("");
          setAttachments((current) =>
            current.filter(
              (attachment) => !submittedAttachmentIds.has(attachment.id),
            ),
          );
          if (draftSnapshot.selectedSkillRevision === submittedSkillRevision) {
            onClearSelectedSkill?.();
          }
          requestAnimationFrame(() => {
            if (textareaRef.current) textareaRef.current.style.height = "auto";
          });
        }
      } finally {
        releaseChatInputSubmissionLock(isSubmittingRef, submissionToken);
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!disableSlashCommands && slashMenuOpen) {
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
    requestAnimationFrame(scheduleTextareaResize);
  }, [closeSlashMenu, scheduleTextareaResize, setInput]);

  const openCommandPanel = useCallback(
    (nextValue: string): boolean => {
      if (disableSlashCommands) return false;
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
      disableSlashCommands,
      scheduleTextareaResize,
      setInput,
      upsertUnavailableCommandChip,
    ],
  );

  const slashCommandItems = useMemo(
    () =>
      !disableSlashCommands && slashMenuOpen
        ? resolveSlashCommandMenu(input, commandPanelAvailability)
        : [],
    [commandPanelAvailability, disableSlashCommands, input, slashMenuOpen],
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
        requestAnimationFrame(scheduleTextareaResize);
        return;
      }
      setInput(nextInput);
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
      scheduleTextareaResize,
      setInput,
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
      requestAnimationFrame(scheduleTextareaResize);
      return true;
    },
    [
      commandPanelAvailability,
      closeSlashMenu,
      executeAvailableFileCommand,
      handleSlashCommandSelect,
      scheduleTextareaResize,
      setInput,
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
      setActivePanel(panel);
      closeSlashMenu();
    },
    [closeSlashMenu],
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
      setInput,
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
        description: fileRequirement,
        visibleDetails: [fileRequirement],
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
      setActivePanel(null);
      setCommandSearchSeed(null);
      closeSlashMenu();
      requestAnimationFrame(scheduleTextareaResize);
    },
    [closeSlashMenu, onSelectModel, scheduleTextareaResize, setInput],
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
    if (acceptedFileTypes?.length === 0) return;
    if (!validateCount(files.length)) return;
    uploadFiles(files);
  };

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
          {!disableSlashCommands && slashMenuOpen && (
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
                      "This Skill was updated. Select it again before submitting.",
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
                  if (!openCommandPanel(nextValue)) {
                    closeSlashMenu();
                  }
                }}
                onFocus={scheduleTextareaResize}
                onKeyDown={handleKeyDown}
                onPaste={handlePaste}
                placeholder={
                  canSend
                    ? placeholder ?? t("chat.placeholder")
                    : placeholder ?? t("chat.noPermission")
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
                agentOptions={agentOptions}
                agentOptionValues={agentOptionValues}
                onToggleAgentOption={onToggleAgentOption}
                uploadCategories={uploadCategories}
                uploadLimitsBytes={uploadLimitsBytes}
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
        loading={isStopSubmitting}
        onConfirm={() => {
          void (async () => {
            setIsStopSubmitting(true);
            let result: Awaited<ReturnType<typeof onStop>> = "unconfirmed";
            try {
              result = await onStop();
            } catch {
              // The lifecycle normally converts transport uncertainty into an
              // explicit result. Keep this UI truthful if a caller regresses.
            }

            if (result !== "acknowledged") {
              setIsStopSubmitting(false);
              toast.error(
                result === "unavailable"
                  ? t("chat.stopUnavailable")
                  : t("chat.stopUnconfirmed"),
              );
              return;
            }

            setStopConfirmOpen(false);
            setIsStopSubmitting(false);
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
                <span>{t("chat.runStatus.event.cancelRequested")}</span>
              </div>
            ));
          })();
        }}
        onCancel={() => {
          setStopConfirmOpen(false);
          setIsStopSubmitting(false);
        }}
      />

      <ContactAdminDialog
        isOpen={contactAdminOpen}
        onClose={() => setContactAdminOpen(false)}
        reason="noPermission"
      />
    </LibreChatComposerFrame>
  );
});
