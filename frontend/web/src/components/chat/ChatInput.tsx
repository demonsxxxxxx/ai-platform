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
import { useAui } from "@assistant-ui/react";
import { createPlatformAttachmentAdapter } from "./assistant-ui/attachmentBinding";
import { submitComposerMessage } from "./assistant-ui/composerSubmission";
import { usePlatformComposer } from "./assistant-ui/platformComposer";
import { toast } from "@lobehub/ui/base-ui";
import { Button } from "@lobehub/ui/base-ui";
import { Square } from "lucide-react";
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
import { ChatInputAttachments } from "./ChatInputAttachments";
import {
  parseComposerCommand,
  clearModelCommandDraft,
  resolveComposerCommandDraft,
  resolveSlashCommandMenu,
  type ComposerCommandPanel,
  type SlashCommandMenuItem,
} from "./chatInputCommands";
import { ComposerChips } from "./ComposerChips";
import { SlashCommandMenu } from "./SlashCommandMenu";
import {
  composerSelectionReducer,
  type ComposerSelectionKind,
} from "./composerSelections";
import { FILE_CATEGORY_PERMISSIONS } from "./chatInputConstants";
import { reconcileChatInputSubmissionLock } from "./chatInputSubmissionLock";
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
  ToolState,
  FileCategory,
  PublicSkillResponse,
} from "../../types";
import {
  getProfileDriveDragReference,
  hasProfileDriveDragData,
} from "../workbench/profileDriveDrag";
import { Composer, ComposerBar, ComposerInput } from "./assistant-ui/elements/composer";

export type {
  ChatInputDraftSnapshot,
  ChatInputProps,
} from "./chatInputTypes";

const EMPTY_TOOLS: ToolState[] = [];

export const ChatInput = memo(function ChatInput({
  initialDraft,
  initialDraftKey,
  draftSnapshotRef,
  draftScopeKey,
  attachmentScopeKey,
  draftScopeHandoffKey,
  onSend,
  onStop,
  isLoading,
  canStop = false,
  runControlKey,
  disabled,
  canSend = true,
  placeholder,
  modelUnavailableReason,
  unavailableReason,
  acceptedFileTypes,
  disableSlashCommands = false,
  tools = EMPTY_TOOLS,
  onToggleTool,
  onToggleCategory,
  onToggleAll,
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
  onModelSelectorOpen,
  attachments: externalAttachments,
  onAttachmentsChange: externalOnAttachmentsChange,
  uploadControls: sharedUploadControls,
  onProfileDriveFileDrop,
  pendingInput,
  onPendingInputConsumed,
  className,
  taskSurface,
  questionMode = false,
}: ChatInputProps) {
  const { t } = useTranslation();
  const { hasPermission, user } = useAuth();
  const ownerKey = `${user?.tenant_id ?? ""}:${user?.id ?? ""}`;
  const aui = useAui();
  const composerRuntime = aui.composer;
  const { register: registerComposer } = usePlatformComposer();
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
    const ownerChanged = draftSnapshot.ownerKey !== undefined && draftSnapshot.ownerKey !== ownerKey;
    const scopeChanged = draftSnapshot.scopeKey !== draftScopeKey;
    draftSnapshot.ownerKey = ownerKey;
    const preserveFirstSubmission =
      !ownerChanged &&
      draftSnapshot.scopeKey == null &&
      draftScopeKey != null &&
      draftScopeKey === draftScopeHandoffKey &&
      draftSnapshot.pendingScopeHandoff;
    if (scopeChanged || ownerChanged) {
      void aui.composer.reset();
      draftSnapshot.scopeKey = draftScopeKey;
      draftSnapshot.pendingScopeHandoff = false;
      if (!preserveFirstSubmission) {
        draftSnapshot.scopeRevision = (draftSnapshot.scopeRevision ?? 0) + 1;
        draftSnapshot.selectedSkillRevision += 1;
        draftSnapshot.value = "";
        draftSnapshot.revision += 1;
        draftSnapshot.appliedInitialDraftKey = null;
      }
    }

    const apply = (value: string) => {
      inputRef.current = value;
      setLocalInput(value);
      aui.composer.setText(value);
    };
    draftSnapshot.apply = apply;
    if (inputRef.current !== draftSnapshot.value) {
      apply(draftSnapshot.value);
    }
    aui.composer.setText(draftSnapshot.value);
    return () => {
      if (draftSnapshot.apply === apply) draftSnapshot.apply = undefined;
    };
  }, [aui, draftScopeHandoffKey, draftScopeKey, draftSnapshot, ownerKey]);

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
    if (pendingInput && !questionMode) {
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
  }, [pendingInput, onPendingInputConsumed, questionMode, setInput]);

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
  const stopOwnerRef = useRef(runControlKey);
  stopOwnerRef.current = runControlKey;

  useEffect(() => {
    setStopConfirmOpen(false);
    setIsStopSubmitting(false);
  }, [draftScopeKey, ownerKey, runControlKey]);

  useEffect(() => {
    reconcileChatInputSubmissionLock(isSubmittingRef, isLoading);
  }, [isLoading]);

  useEffect(() => {
    // An arriving question owns the surface: no selector or command popover may
    // stay open over it. Panel state itself is kept for the ordinary composer.
    if (!questionMode) return;
    setActivePanel(null);
    setSlashMenuOpen(false);
    setIsDraggingOver(false);
  }, [questionMode]);

  const uploadCategories = (
    acceptedFileTypes?.length === 0
      ? []
      : (Object.keys(FILE_CATEGORY_PERMISSIONS) as Array<
          keyof typeof FILE_CATEGORY_PERMISSIONS
        >)
  ).filter((cat) => hasPermission(FILE_CATEGORY_PERMISSIONS[cat]));

  const attachments = externalAttachments ?? internalAttachments;
  const setAttachments = externalOnAttachmentsChange ?? setInternalAttachments;

  const {
    uploadFiles: uploadPlatformFiles,
    uploadFile,
    getUploadHandle,
    releaseUploadHandle,
    validateCount,
    cancelUpload: cancelPlatformUpload,
    clearUploads,
    removeAttachment: removePlatformAttachment,
  } = useFileUpload({
    attachments,
    onAttachmentsChange: setAttachments,
    acceptedFileTypes,
    sharedControls: sharedUploadControls,
  });

  const attachmentAdapter = useMemo(() => createPlatformAttachmentAdapter({
    uploadControls: { uploadFile, getUploadHandle, releaseUploadHandle, cancelUpload: cancelPlatformUpload, removeAttachment: removePlatformAttachment },
    completeAttachment: async (attachment) => ({
      id: attachment.id, type: attachment.type === "image" ? "image" : "document", name: attachment.name,
      contentType: attachment.mimeType, content: [], status: { type: "complete" },
    }),
  }), [uploadFile, getUploadHandle, releaseUploadHandle, cancelPlatformUpload, removePlatformAttachment]);

  const uploadFiles = useCallback((files: FileList | File[], category?: FileCategory) => {
    if (questionMode) return;
    // Profile type/count/size validation remains in the shared upload controller.
    uploadPlatformFiles(files, category, (file) => {
      const scopeRevision = draftSnapshot.scopeRevision;
      void composerRuntime.addAttachment(file).then(() => {
        if (draftSnapshot.scopeRevision !== scopeRevision) return;
        const added = composerRuntime.getState().attachments.find((item) => item.file === file);
        const handle = added && getUploadHandle(added.id);
        void handle?.done.then((result) => {
          if (result || draftSnapshot.scopeRevision !== scopeRevision) return;
          const index = composerRuntime.getState().attachments.findIndex((item) => item.id === added?.id);
          if (index >= 0) void composerRuntime.attachment({ index }).remove();
        });
      }).catch(() => { /* The upload controller already reports validation/upload failures. */ });
    });
  }, [composerRuntime, draftSnapshot, getUploadHandle, questionMode, uploadPlatformFiles]);

  const removeAttachment = useCallback((attachment: MessageAttachment) => {
    const handle = getUploadHandle(attachment.id);
    const native = composerRuntime.getState().attachments;
    const index = native.findIndex((item) => item.id === attachment.id || item.id === handle?.tempId);
    if (index >= 0) {
      void composerRuntime.attachment({ index }).remove();
      if (native[index].status.type !== "complete") return;
    }
    removePlatformAttachment(attachment);
  }, [composerRuntime, getUploadHandle, removePlatformAttachment]);
  const cancelUpload = useCallback((id: string) => {
    const attachment = attachments.find((item) => item.id === id);
    if (attachment) removeAttachment(attachment);
    else cancelPlatformUpload(id);
  }, [attachments, cancelPlatformUpload, removeAttachment]);

  const previousAttachmentScopeKeyRef = useRef(attachmentScopeKey);
  useLayoutEffect(() => {
    if (previousAttachmentScopeKeyRef.current === attachmentScopeKey) return;
    previousAttachmentScopeKeyRef.current = attachmentScopeKey;
    clearUploads();
  }, [attachmentScopeKey, clearUploads]);

  const { pushHistory, navigateUp, navigateDown } = useInputHistory(user);

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
    if (questionMode) return;
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
  }, [questionMode, scheduleTextareaResize, setInput]);

  const hasUploadingAttachment = attachments.some((a) => a.isUploading);

  const submitToPlatform = (content: string) => submitComposerMessage({
    content,
    snapshot: draftSnapshot,
    attachments,
    allowed: !questionMode && canSend && !modelUnavailableReason && !hasUploadingAttachment && !isLoading && !disabled,
    lock: isSubmittingRef,
    onSend,
    agentOptionValues,
    selectedSkillState,
    onSelectedSkillRecoverable,
    onClearSelectedSkill,
    setAttachments,
    setInput,
    pushHistory,
    restoreText: (value) => aui.composer.setText(value),
  });

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (questionMode) return;
    // IME candidate keys belong to the native editor, including the legacy 229
    // signal used when composition ends before the confirming keydown event.
    if (e.nativeEvent.isComposing || e.nativeEvent.keyCode === 229) {
      e.stopPropagation();
      return;
    }

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
      if (isLoading && canStop) {
        setStopConfirmOpen(true);
      } else if (isLoading) {
        return;
      } else {
        textareaRef.current?.closest("form")?.requestSubmit();
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
      const prev = navigateUp(input);
      if (prev !== null) {
        e.preventDefault();
        setInput(prev);
        requestAnimationFrame(() => {
          if (textarea) {
            textarea.selectionStart = textarea.selectionEnd = prev.length;
          }
        });
      }
    } else if (e.key === "ArrowDown" && atBottom) {
      const next = navigateDown();
      if (next !== null) {
        e.preventDefault();
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
  const skillsAvailable =
    enableSkills && !!onSelectSkill;
  const toolsAvailable = !!onToggleTool && !!onToggleCategory && !!onToggleAll;
  const commandPanelAvailability = useMemo(
    () => ({
      skills: skillsAvailable,
      tools: toolsAvailable,
      models: !!onSelectModel,
      files: uploadCategories.length > 0,
    }),
    [
      onSelectModel,
      skillsAvailable,
      toolsAvailable,
      uploadCategories.length,
    ],
  );
  const canSubmit =
    hasContent && canSend && !modelUnavailableReason && !isLoading && !hasUploadingAttachment;

  // A pending question batch owns the surface: nothing but the question card may
  // reach the server, so the native form, Enter shortcut, drops and paste keep the
  // ordinary draft intact while its controls stay mounted but hidden.
  const canSubmitOrdinary = canSubmit && !questionMode;

  useLayoutEffect(() => registerComposer({
    send: submitToPlatform,
    requestCancel: () => setStopConfirmOpen(true),
    getText: () => draftSnapshot.value,
    setText: (text) => setInput(text),
    disabled: Boolean(disabled || !canSend),
    sendDisabled: !canSend || Boolean(modelUnavailableReason) || isLoading || hasUploadingAttachment || questionMode,
    canCancel: Boolean(canStop && isLoading),
    attachmentAdapter: uploadCategories.length > 0 ? attachmentAdapter : undefined,
  }));

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
        if (activePanel !== "model" || commandSearchSeed) {
          requestAnimationFrame(() => textareaRef.current?.focus());
        }
        return;
      }
      if (panel === "file") {
        openFileCommandRef.current?.();
        return;
      }
      setActivePanel(panel);
      closeSlashMenu();
    },
    [activePanel, closeSlashMenu, commandSearchSeed],
  );

  useEffect(() => {
    if (activePanel === "model") void onModelSelectorOpen?.();
  }, [activePanel, onModelSelectorOpen]);

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
    // Composer file attachments have one presentation owner: ChatInputAttachments.
    // A mirrored file chip would duplicate the same removable file in the composer.
    dispatchComposerSelection({ type: "clear-kind", kind: "file" });
  }, [attachments]);

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
      setInput((current) =>
        clearModelCommandDraft(
          current,
          commandPanelAvailability,
          !disableSlashCommands,
        ),
      );
      setActivePanel(null);
      setCommandSearchSeed(null);
      closeSlashMenu();
      requestAnimationFrame(scheduleTextareaResize);
    },
    [
      closeSlashMenu,
      commandPanelAvailability,
      disableSlashCommands,
      onSelectModel,
      scheduleTextareaResize,
      setInput,
    ],
  );

  const handleRemoveComposerSelection = useCallback(
    (id: string) => {
      dispatchComposerSelection({ type: "remove", id });
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
      tools,
    ],
  );

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (questionMode) {
      e.stopPropagation();
      e.dataTransfer.dropEffect = "none";
      return;
    }
    if (hasProfileDriveDragData(e.dataTransfer)) {
      e.dataTransfer.dropEffect = "copy";
    }
    setIsDraggingOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDraggingOver(false);
    if (questionMode) {
      e.stopPropagation();
      return;
    }
    const profileDriveReference = getProfileDriveDragReference(e.dataTransfer);
    if (profileDriveReference && onProfileDriveFileDrop) {
      e.stopPropagation();
      void onProfileDriveFileDrop(profileDriveReference);
      return;
    }
    const files = e.dataTransfer?.files;
    if (!files || files.length === 0) return;
    if (acceptedFileTypes?.length === 0) return;
    if (!validateCount(files.length)) return;
    uploadFiles(files);
  };

  // The question footer reuses this Composer's own Run cancellation path
  // (confirmation dialog, lifecycle receipt, scope guards) instead of a second
  // controller. It appears exactly where the toolbar's Stop button would.
  const questionStopControl = isLoading && canStop ? (
    <Button
      type="text"
      htmlType="button"
      data-run-question-stop
      onClick={() => setStopConfirmOpen(true)}
      icon={<Square aria-hidden size={12} fill="currentColor" />}
      style={{ height: "auto", minHeight: 40, padding: "4px 0", color: "var(--theme-text-secondary)" }}
    >
      {t("chat.stop")}
    </Button>
  ) : null;

  return (
    <div className="chat-input-shell" style={{ backgroundColor: "var(--theme-workbench-canvas)" }}>
      <Composer
        onSubmit={(event) => {
          // Question mode owns the surface: the hidden ordinary form never sends.
          if (!canSubmitOrdinary || (!disableSlashCommands && handleComposerCommandSubmit(input))) event.preventDefault();
        }}
        className={
          className ?? "mx-auto max-w-[76rem] px-2"
        }
      >
        <div
          className="relative"
          data-composer-command-menu-anchor
        >
          {!disableSlashCommands && slashMenuOpen && !questionMode && (
            <SlashCommandMenu
              items={slashCommandItems}
              highlightedIndex={slashMenuHighlight}
              onHighlight={setSlashMenuHighlight}
              onSelect={handleSlashCommandSelect}
              onClose={closeSlashMenu}
            />
          )}
          <ComposerBar
            ref={containerRef}
            data-disable-global-file-drop="true"
            data-question-mode={questionMode || undefined}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            dragActive={isDraggingOver}
          >
            {taskSurface ? (
              /* The task surface replaces the ordinary controls in question mode; it
                 keeps its natural height so short questions show every choice and
                 action without an inner scroll. */
              <div className="flex flex-col" data-slot="composer-task-surface">
                {taskSurface(questionStopControl)}
              </div>
            ) : null}
            {/* The ordinary controls stay mounted (draft, uploads, selectors) and are
                only hidden, so leaving question mode restores them untouched. */}
            <div
              className="flex flex-col gap-0"
              style={{ display: questionMode ? "none" : undefined }}
              data-slot="composer-ordinary-controls"
            >
            <div data-slot="composer-attachments">
              <ChatInputAttachments
                attachments={attachments}
                onRemoveAttachment={removeAttachment}
                onCancelUpload={cancelUpload}
                onImageViewerOpen={(url) => setImageViewerSrc(url)}
              />
            </div>

            <div data-slot="composer-chips">
              <ComposerChips
                selections={composerSelections}
                onRemove={handleRemoveComposerSelection}
              />
            </div>

            {selectedSkillState?.recoveryCode && (
              <div
                className="col-span-2 mx-3 mt-2 rounded-lg border border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] px-3 py-2 text-xs leading-relaxed text-[var(--theme-warning)]"
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

            <div data-slot="composer-input">
                <ComposerInput
                  ref={textareaRef}
                  aria-label={t("chat.messageInput", "消息")}
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
                onPaste={questionMode ? undefined : handlePaste}
                placeholder={
                  canSend
                    ? placeholder ?? t("chat.placeholder")
                    : placeholder ?? t("chat.noPermission")
                }
                disabled={questionMode || disabled || !canSend}
                rows={1}
                />
            </div>

            <div data-slot="composer-toolbar-region" className="min-w-0">
              <ChatInputToolbar
                activePanel={activePanel}
                onActivePanelChange={handlePanelChange}
                canSend={canSend}
                unavailableReason={unavailableReason}
                isLoading={isLoading}
                canStop={canStop}
                canSubmit={canSubmit}
                modelUnavailableReason={modelUnavailableReason}
                hasUploadingAttachment={hasUploadingAttachment}
                enabledToolsCount={enabledToolsCount}
                totalToolsCount={totalToolsCount}
                enabledSkillsCount={enabledSkillsCount}
                totalSkillsCount={totalSkillsCount}
                availableModels={availableModels}
                currentModelId={currentModelId}
                onSelectModel={onSelectModel}
                showModelSelector
                agentOptions={agentOptions}
                agentOptionValues={agentOptionValues}
                onToggleAgentOption={onToggleAgentOption}
                uploadCategories={uploadCategories}
                uploadFiles={uploadFiles}
                onFileCommandReady={(openFileCommand) => {
                  openFileCommandRef.current = openFileCommand;
                }}
                onNoPermissionClick={() => setContactAdminOpen(true)}
              />
            </div>
            </div>
          </ComposerBar>
        </div>
      </Composer>

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
            const stopOwner = stopOwnerRef.current;
            const scopeRevision = draftSnapshot.scopeRevision;
            setIsStopSubmitting(true);
            let result: Awaited<ReturnType<typeof onStop>> = "unconfirmed";
            try {
              result = await onStop();
            } catch {
              // The lifecycle normally converts transport uncertainty into an
              // explicit result. Keep this UI truthful if a caller regresses.
            }

            if (stopOwnerRef.current !== stopOwner || draftSnapshot.scopeRevision !== scopeRevision) return;
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
            toast.info({ description: t("chat.runStatus.event.cancelRequested"), hideCloseButton: true });
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
    </div>
  );
});
