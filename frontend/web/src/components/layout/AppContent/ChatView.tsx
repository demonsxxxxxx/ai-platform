import {
  useMemo,
  useCallback,
  useState,
  useEffect,
  useRef,
  type ComponentType,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { ListTree } from "lucide-react";
import toast from "react-hot-toast";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../../hooks/useAuth";
import { ChatMessage } from "../../chat/ChatMessage";
import { AttachmentPreviewHost } from "../../chat/AttachmentPreviewHost";
import { RevealPreviewHost } from "../../chat/ChatMessage/items/RevealPreviewHost";
import { SessionImageGalleryProvider } from "../../chat/ChatMessage/sessionImageGallery";
import {
  PersistentToolPanelHost,
  closePersistentToolPanel,
  openPersistentToolPanel,
  isPersistentToolPanelOpen,
  updatePersistentToolPanel,
  type PersistentToolPanelState,
} from "../../chat/ChatMessage/items/persistentToolPanelState";
import { ChatInput } from "../../chat/ChatInput";
import { WelcomePage } from "../../chat/WelcomePage";
import { WorkbenchRightPanel } from "../../workbench/WorkbenchRightPanel";
import { Virtuoso, type ListRange } from "react-virtuoso";
import { ApprovalPanel } from "../../panels/ApprovalPanel";
import {
  ChatSkeleton,
  ChatSkeletonMessagesOnly,
} from "../../skeletons/ChatSkeletons";
import { useMessageScroll } from "./useMessageScroll";
import {
  getAtBottomThresholdPx,
  getInitialBottomItemLocation,
  getMessageListFooterSpacerClass,
} from "./messageScrollUtils";
import { getNextMessageListSessionKey } from "./useMessageScroll";
import {
  createMessageAnchorId,
  getOutlineActiveAnchorIdForRange,
  shouldShowMessageOutline,
  extractMessageOutline,
} from "./messageOutline";
import { MessageOutlinePanel } from "./MessageOutlinePanel";
import {
  isSessionRunning,
  shouldShowStreamingFooterSkeleton,
} from "./sessionState";
import type {
  Message,
  PendingApproval,
  ToolState,
  SkillResponse,
  PublicSkillResponse,
  SelectedSkillRequest,
  ToolCategory,
  AgentOption,
  MessageAttachment,
  ConnectionStatus,
} from "../../../types";
import type { SubmissionOutcome } from "../../../hooks/useAgent/types";
import type {
  SelectedSkillRecoverableCode,
  SelectedSkillTaskState,
} from "../../../hooks/useSelectedSkillTask";
import type { RevealPreviewRequest } from "../../chat/ChatMessage/items/revealPreviewData";
import { clearFileRevealAutoOpenState } from "../../chat/ChatMessage/items/fileRevealAutoOpen";
import { clearProjectRevealAutoOpenState } from "../../chat/ChatMessage/items/projectRevealAutoOpen";
import { getLatestChatAutoPreviewTarget } from "../../chat/ChatMessage/autoPreviewEligibility";
import {
  createActiveRevealPreviewState,
  markRevealPreviewInteracted,
  shouldAcceptRevealPreviewOpen,
  shouldStabilizeScrollForAutoPreviewOpen,
  type ActiveRevealPreviewState,
  type RevealPreviewOpenSource,
} from "../../chat/ChatMessage/items/revealPreviewState";
import {
  getActiveRevealPreviewState,
  setActiveRevealPreviewState,
  subscribeActiveRevealPreviewState,
  updateActiveRevealPreviewState,
} from "../../chat/ChatMessage/items/activeRevealPreviewStore";
import { clearSidebarHistory } from "../../chat/ChatMessage/items/sidebarHistoryStore";
import type { ExternalNavigationTargetFile } from "./externalNavigationState";
import { isFileLink } from "../../documents/utils";
import { sessionApi } from "../../../services/api";
import type { SessionInputFile } from "../../../services/api";
import { buildFileLinkPreviewRequest } from "../../chat/ChatMessage/items/fileLinkPreview";
import type { ModelOption } from "../../../services/api/modelPublic";
import { openAttachmentPreview } from "../../chat/attachmentPreviewStore";
import { downloadPreviewUrl } from "../../documents/documentPreviewSources";
import {
  mergeProjectedSessionFiles,
  sessionInputFileToAttachment,
} from "./sessionInputFiles";

const FLOATING_SCROLL_BUTTON_OFFSET_CLASS = "bottom-full mb-3";

interface ChatViewProps {
  messages: Message[];
  sessionId: string | null;
  currentRunId: string | null;
  isLoading: boolean;
  isLoadingHistory: boolean;
  connectionStatus?: ConnectionStatus;
  canSendMessage: boolean;
  tools: ToolState[];
  onToggleTool: (name: string) => void;
  onToggleCategory: (category: ToolCategory, enabled: boolean) => void;
  onToggleAll: (enabled: boolean) => void;
  toolsLoading: boolean;
  enabledToolsCount: number;
  totalToolsCount: number;
  skills: SkillResponse[];
  taskSkills: PublicSkillResponse[];
  selectedSkillState: SelectedSkillTaskState;
  onSelectSkill: (skill: PublicSkillResponse) => void;
  onClearSelectedSkill: () => void;
  onSelectedSkillRecoverable: (
    code: SelectedSkillRecoverableCode,
  ) => Promise<unknown>;
  onSelectedSkillFilesReady: () => void;
  skillsLoading: boolean;
  enabledSkillsCount: number;
  totalSkillsCount: number;
  enableSkills: boolean;
  agentOptions: Record<string, AgentOption>;
  agentOptionValues: Record<string, boolean | string | number>;
  onToggleAgentOption: (key: string, value: boolean | string | number) => void;
  availableModels: ModelOption[];
  currentModelId: string;
  onSelectModel: (modelId: string, modelValue: string) => void;
  approvals: PendingApproval[];
  onRespondApproval: (
    id: string,
    response: Record<string, unknown>,
    approved: boolean,
  ) => void;
  approvalLoading: boolean;
  onSendMessage: (
    content: string,
    options?: Record<string, boolean | string | number>,
    attachments?: MessageAttachment[],
    selectedSkill?: SelectedSkillRequest | null,
  ) => Promise<SubmissionOutcome>;
  canRetryPendingSubmission: boolean;
  onRetryPendingSubmission: () => Promise<void>;
  onStopGeneration: () => void;
  attachments: MessageAttachment[];
  onAttachmentsChange: React.Dispatch<
    React.SetStateAction<MessageAttachment[]>
  >;
  externalNavigationToken?: string | null;
  externalNavigationTargetFile?: ExternalNavigationTargetFile | null;
  externalNavigationTargetRunId?: string | null;
  externalNavigationTargetRunPending?: boolean;
  externalScrollToBottom?: boolean;
  outlineToggleRef?: React.RefObject<(() => void) | null>;
  WorkbenchShellComponent: ComponentType<{
    children: ReactNode;
    composer?: ReactNode;
    rightPanel?: ReactNode;
  }>;
}

export function ChatView({
  messages,
  sessionId,
  currentRunId,
  isLoading,
  isLoadingHistory,
  connectionStatus,
  canSendMessage,
  tools,
  onToggleTool,
  onToggleCategory,
  onToggleAll,
  toolsLoading,
  enabledToolsCount,
  totalToolsCount,
  skills,
  taskSkills,
  selectedSkillState,
  onSelectSkill,
  onClearSelectedSkill,
  onSelectedSkillRecoverable,
  onSelectedSkillFilesReady,
  skillsLoading,
  enabledSkillsCount,
  totalSkillsCount,
  enableSkills,
  agentOptions,
  agentOptionValues,
  onToggleAgentOption,
  availableModels,
  currentModelId,
  onSelectModel,
  approvals,
  onRespondApproval,
  approvalLoading,
  onSendMessage,
  canRetryPendingSubmission,
  onRetryPendingSubmission,
  onStopGeneration,
  attachments,
  onAttachmentsChange,
  externalNavigationToken,
  externalNavigationTargetFile,
  externalNavigationTargetRunId,
  externalNavigationTargetRunPending,
  externalScrollToBottom,
  outlineToggleRef,
  WorkbenchShellComponent,
}: ChatViewProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [composerDraft, setComposerDraft] = useState("");
  const [sessionFiles, setSessionFiles] = useState<SessionInputFile[]>([]);
  const [sessionFilesStatus, setSessionFilesStatus] = useState<
    "idle" | "loading" | "ready" | "error"
  >("idle");
  const sessionRunning = isSessionRunning(messages, isLoading);
  const hasVisibleStreamingMessage = messages.some(
    (message) => message.role === "assistant" && message.isStreaming,
  );

  const showStreamingFooterSkeleton = shouldShowStreamingFooterSkeleton({
    connectionStatus,
    sessionRunning,
    messageCount: messages.length,
    hasVisibleStreamingMessage,
  });

  const getGreetingKey = () => {
    const h = new Date().getHours();
    if (h < 6) return "chat.goodEvening";
    if (h < 12) return "chat.goodMorning";
    if (h < 18) return "chat.goodAfternoon";
    return "chat.goodEvening";
  };
  const greeting = user?.username
    ? t(getGreetingKey(), { name: user.username })
    : t(getGreetingKey());

  const showOutline = shouldShowMessageOutline(messages);
  const outlineItems = useMemo(
    () => (showOutline ? extractMessageOutline(messages) : []),
    [messages, showOutline],
  );
  const previousSessionIdRef = useRef<string | null | undefined>(sessionId);
  const [messageListSessionKey, setMessageListSessionKey] = useState(
    sessionId ?? "__new_session__",
  );

  const {
    messagesContainerRef,
    virtuosoRef,
    virtuosoScrollerRef,
    messagesEndRef,
    isNearBottom,
    showScrollTop,
    handleVirtuosoAtBottomChange,
    scrollToBottom,
    scrollToTop,
  } = useMessageScroll(
    messages,
    sessionId,
    externalNavigationToken,
    externalNavigationTargetFile,
    externalNavigationTargetRunId,
    externalNavigationTargetRunPending,
    externalScrollToBottom,
    isLoadingHistory,
    messageListSessionKey,
  );
  const [visibleRange, setVisibleRange] = useState<ListRange | null>(null);

  useEffect(() => {
    const previousSessionId = previousSessionIdRef.current;
    previousSessionIdRef.current = sessionId;
    setMessageListSessionKey((previousKey) => {
      const nextKey = getNextMessageListSessionKey({
        previousSessionId,
        sessionId,
        messageCount: messages.length,
        previousKey,
      });
      return nextKey === previousKey ? previousKey : nextKey;
    });
  }, [messages.length, sessionId]);

  useEffect(() => {
    let current = true;
    if (!sessionId) {
      setSessionFiles([]);
      setSessionFilesStatus("ready");
      return () => {
        current = false;
      };
    }
    setSessionFiles([]);
    setSessionFilesStatus("loading");
    void sessionApi
      .getInputFiles(sessionId)
      .then((projection) => {
        if (!current || projection.session_id !== sessionId) return;
        setSessionFiles(projection.files);
        setSessionFilesStatus("ready");
      })
      .catch(() => {
        if (!current) return;
        setSessionFiles([]);
        setSessionFilesStatus("error");
      });
    return () => {
      current = false;
    };
  }, [sessionId, currentRunId, messages.length, attachments.length]);

  const displayMessages = useMemo(
    () => mergeProjectedSessionFiles(messages, sessionFiles),
    [messages, sessionFiles],
  );

  const activeOutlineId = useMemo(() => {
    const rangeActiveId = getOutlineActiveAnchorIdForRange(
      messages,
      visibleRange,
    );
    if (rangeActiveId) {
      return rangeActiveId;
    }

    const latestMessage = messages[messages.length - 1];
    return latestMessage ? createMessageAnchorId(latestMessage.id) : null;
  }, [messages, visibleRange]);

  const handleOutlineNavigate = useCallback(
    (anchorId: string, messageIndex: number) => {
      virtuosoRef.current?.scrollToIndex({
        index: messageIndex,
        behavior: "smooth",
        align: "start",
      });
      // After Virtuoso renders the message, scroll to the specific heading anchor
      requestAnimationFrame(() => {
        const el = document.getElementById(anchorId);
        if (el) {
          el.scrollIntoView({ behavior: "smooth", block: "start" });
        }
      });
      requestAnimationFrame(() => {
        closePersistentToolPanel();
      });
    },
    [virtuosoRef],
  );

  const handleOpenOutline = useCallback(() => {
    if (isPersistentToolPanelOpen("outline")) {
      closePersistentToolPanel();
      return;
    }
    const isMobile = window.innerWidth < 640;
    openPersistentToolPanel({
      title: t("chat.outline"),
      icon: <ListTree size={18} strokeWidth={2} />,
      status: "idle",
      panelKey: "outline",
      viewMode: isMobile ? "center" : "sidebar",
      children: (
        <MessageOutlinePanel
          items={outlineItems}
          activeId={activeOutlineId}
          onNavigate={handleOutlineNavigate}
        />
      ),
    });
  }, [
    outlineItems,
    activeOutlineId,
    handleOutlineNavigate,
    t,
  ]);

  useEffect(() => {
    if (outlineToggleRef) {
      outlineToggleRef.current = showOutline ? handleOpenOutline : null;
    }
  }, [outlineToggleRef, showOutline, handleOpenOutline]);

  useEffect(() => {
    if (!isPersistentToolPanelOpen("outline")) return;
    updatePersistentToolPanel(
      (prev: PersistentToolPanelState) => ({
        ...prev,
        children: (
          <MessageOutlinePanel
            items={outlineItems}
            activeId={activeOutlineId}
            onNavigate={handleOutlineNavigate}
          />
        ),
      }),
      "outline",
    );
  }, [outlineItems, activeOutlineId, handleOutlineNavigate]);

  const [, forcePreviewRender] = useState(0);
  const activePreviewStateRef = useRef<ActiveRevealPreviewState | null>(
    getActiveRevealPreviewState(),
  );
  const isNearBottomRef = useRef(isNearBottom);
  const autoPreviewScrollStabilizerRef = useRef<ReturnType<
    typeof setTimeout
  > | null>(null);
  const dismissedPreviewKeysRef = useRef<Set<string>>(new Set());
  const activePreview = activePreviewStateRef.current?.request ?? null;

  useEffect(() => {
    isNearBottomRef.current = isNearBottom;
  }, [isNearBottom]);

  useEffect(() => {
    const syncPreviewState = () => {
      const previousPreview = activePreviewStateRef.current;
      const nextPreview = getActiveRevealPreviewState();
      activePreviewStateRef.current = nextPreview;
      forcePreviewRender((count) => count + 1);

      if (
        shouldStabilizeScrollForAutoPreviewOpen({
          previousPreview,
          nextPreview,
          isNearBottom: isNearBottomRef.current,
        })
      ) {
        if (autoPreviewScrollStabilizerRef.current) {
          clearTimeout(autoPreviewScrollStabilizerRef.current);
        }
        autoPreviewScrollStabilizerRef.current = setTimeout(() => {
          autoPreviewScrollStabilizerRef.current = null;
          scrollToBottom();
        }, 360);
      }
    };

    const unsubscribe = subscribeActiveRevealPreviewState(syncPreviewState);
    return () => {
      unsubscribe();
      if (autoPreviewScrollStabilizerRef.current) {
        clearTimeout(autoPreviewScrollStabilizerRef.current);
        autoPreviewScrollStabilizerRef.current = null;
      }
    };
  }, [scrollToBottom]);

  const handleOpenPreview = useCallback(
    (
      preview: RevealPreviewRequest,
      source: RevealPreviewOpenSource = "manual",
    ) => {
      const shouldOpen = shouldAcceptRevealPreviewOpen({
        activePreview: activePreviewStateRef.current,
        nextPreview: preview,
        source,
        dismissedPreviewKeys: dismissedPreviewKeysRef.current,
      });

      if (!shouldOpen) {
        return false;
      }

      if (source !== "auto") {
        dismissedPreviewKeysRef.current.delete(preview.previewKey);
      }

      setActiveRevealPreviewState(
        createActiveRevealPreviewState(preview, source),
      );
      return true;
    },
    [],
  );

  const handleClosePreview = useCallback((dismiss = true) => {
    const currentPreview = activePreviewStateRef.current;
    if (dismiss && currentPreview) {
      dismissedPreviewKeysRef.current.add(currentPreview.request.previewKey);
    }
    setActiveRevealPreviewState(null);
  }, []);

  const handlePreviewInteraction = useCallback(() => {
    updateActiveRevealPreviewState((current) =>
      markRevealPreviewInteracted(current),
    );
  }, []);

  // Fallback: intercept file links anywhere in the chat area (covers MCP blocks, subagent panels, etc.)
  useEffect(() => {
    const container = messagesContainerRef.current;
    if (!container) return;

    const handleClick = (e: MouseEvent) => {
      const target = (e.target as HTMLElement).closest("a[href]");
      if (!target) return;
      const href = (target as HTMLAnchorElement).getAttribute("href");
      if (!href) return;

      const fileLinkInfo = isFileLink(href);
      if (!fileLinkInfo.isFile) return;

      e.preventDefault();
      e.stopPropagation();

      const previewRequest = buildFileLinkPreviewRequest({
        href,
        fileName: fileLinkInfo.fileName,
      });
      if (previewRequest) {
        setActiveRevealPreviewState(
          createActiveRevealPreviewState(previewRequest, "manual"),
        );
      }
    };

    container.addEventListener("click", handleClick, true);
    return () => container.removeEventListener("click", handleClick, true);
  }, [messagesContainerRef]);

  useEffect(() => {
    dismissedPreviewKeysRef.current.clear();
    clearFileRevealAutoOpenState();
    clearProjectRevealAutoOpenState();
    clearSidebarHistory();
    setActiveRevealPreviewState(null);
    closePersistentToolPanel();
  }, [sessionId]);

  const latestAutoPreview = useMemo(
    () =>
      getLatestChatAutoPreviewTarget({
        messages,
        suppressAutoPreview: false,
      }),
    [messages],
  );
  const isMobileViewport =
    typeof window !== "undefined" ? window.innerWidth < 640 : false;

  const handleForkMessage = useCallback(
    async (messageId: string) => {
      if (!sessionId) return;
      try {
        const response = await sessionApi.forkMessage(sessionId, messageId);
        toast.success(t("chat.message.forkSuccess"));
        navigate(`/chat/${response.session.id}`);
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t("chat.message.forkFailed"),
        );
      }
    },
    [navigate, sessionId, t],
  );

  const handleOpenSessionFile = useCallback(
    (file: SessionInputFile) => {
      if (!file.preview_url) {
        void downloadPreviewUrl({
          url: file.download_url,
          fileName: file.name,
        }).catch(() =>
          toast.error(t("documents.failedToDownload", "Download failed")),
        );
        return;
      }
      openAttachmentPreview(sessionInputFileToAttachment(file), "session-files");
    },
    [t],
  );

  const handleDownloadSessionFile = useCallback(
    (file: SessionInputFile) => {
      void downloadPreviewUrl({
        url: file.download_url,
        fileName: file.name,
      }).catch(() =>
        toast.error(t("documents.failedToDownload", "Download failed")),
      );
    },
    [t],
  );

  const handleVirtuosoRangeChanged = useCallback((range: ListRange) => {
    setVisibleRange((current) =>
      current?.startIndex === range.startIndex &&
      current?.endIndex === range.endIndex
        ? current
        : range,
    );
  }, []);
  const virtuosoComponents = useMemo(
    () => ({
      Scroller: (
        scrollerProps: React.HTMLAttributes<HTMLDivElement> & {
          children?: React.ReactNode;
          ref?: React.Ref<HTMLDivElement>;
        },
      ) => {
        const { children, ref: vRef, ...props } = scrollerProps;
        return (
          <div
            {...props}
            ref={(el: HTMLDivElement | null) => {
              virtuosoScrollerRef.current = el;
              if (typeof vRef === "function") vRef(el);
              else if (vRef)
                (
                  vRef as React.MutableRefObject<HTMLDivElement | null>
                ).current = el;
            }}
          >
            {children}
          </div>
        );
      },
      Footer: () => (
        <>
          {showStreamingFooterSkeleton && (
            <div className="pb-4">
              <ChatSkeletonMessagesOnly count={3} />
            </div>
          )}
          <div
            ref={messagesEndRef}
            className={getMessageListFooterSpacerClass(isMobileViewport)}
          />
        </>
      ),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [showStreamingFooterSkeleton],
  );

  const virtuosoItemContent = useCallback(
    (index: number, message: (typeof messages)[number]) => (
      <ChatMessage
        message={message}
        sessionId={sessionId ?? undefined}
        runId={currentRunId ?? undefined}
        isLastMessage={index === messages.length - 1}
        activePreview={activePreview}
        latestAutoPreview={latestAutoPreview}
        onOpenPreview={handleOpenPreview}
        onForkMessage={handleForkMessage}
      />
    ),
    [
      sessionId,
      currentRunId,
      messages.length,
      activePreview,
      latestAutoPreview,
      handleOpenPreview,
      handleForkMessage,
    ],
  );

  // Shared ChatInput props to avoid duplication
  const chatInputProps = {
    draft: composerDraft,
    onDraftChange: setComposerDraft,
    onSend: onSendMessage,
    onStop: onStopGeneration,
    isLoading: sessionRunning,
    canSend: canSendMessage,
    tools,
    onToggleTool,
    onToggleCategory,
    onToggleAll,
    toolsLoading,
    enabledToolsCount,
    totalToolsCount,
    skills: taskSkills,
    selectedSkillState,
    onSelectSkill,
    onClearSelectedSkill,
    onSelectedSkillRecoverable,
    onSelectedSkillFilesReady,
    skillsLoading,
    enabledSkillsCount,
    totalSkillsCount,
    enableSkills,
    agentOptions,
    agentOptionValues,
    onToggleAgentOption,
    availableModels,
    currentModelId,
    onSelectModel,
    attachments,
    onAttachmentsChange,
  };

  const rightPanel = (
    <WorkbenchRightPanel
      sessionId={sessionId}
      currentRunId={currentRunId}
      messageCount={messages.length}
      skills={skills}
      tools={tools}
      sessionFiles={sessionFiles}
      sessionFilesStatus={sessionFilesStatus}
      onOpenSessionFile={handleOpenSessionFile}
      onDownloadSessionFile={handleDownloadSessionFile}
      approvals={approvals}
    />
  );

  const composer = (
    <div className="relative" data-chat-shell-composer>
      {messages.length > 0 && showScrollTop && (
        <div
          className={`absolute right-3 sm:right-4 z-50 flex flex-col gap-1.5 ${FLOATING_SCROLL_BUTTON_OFFSET_CLASS}`}
        >
          <button
            onClick={scrollToTop}
            className="flex items-center rounded-full border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-2 text-[var(--theme-text-secondary)] shadow-[0_4px_12px_rgba(18,38,63,0.03)] transition-colors duration-200 hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] active:scale-95 dark:bg-stone-900 dark:hover:bg-stone-800"
            aria-label={t("chat.scrollToTop", "Scroll to top")}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 20 20"
              fill="currentColor"
              className="w-4 h-4 text-stone-500 dark:text-stone-300"
            >
              <path
                fillRule="evenodd"
                d="M10 17a.75.75 0 01-.75-.75V5.612l-3.96 4.158a.75.75 0 11-1.08-1.04l5.25-5.5a.75.75 0 011.08 0l5.25 5.5a.75.75 0 11-1.08 1.04l-3.96-4.158V16.25A.75.75 0 0110 17z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        </div>
      )}

      {messages.length > 0 && !isNearBottom && (
        <button
          onClick={scrollToBottom}
          className={`absolute left-1/2 z-50 flex items-center rounded-full border border-[var(--theme-border)] bg-[var(--theme-bg-card)] p-2 text-[var(--theme-text-secondary)] shadow-[0_4px_12px_rgba(18,38,63,0.03)] transition-colors duration-200 hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] active:scale-95 dark:bg-stone-900 dark:hover:bg-stone-800 ${FLOATING_SCROLL_BUTTON_OFFSET_CLASS} -translate-x-1/2`}
          aria-label={t("chat.scrollToBottom", "Scroll to bottom")}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 20 20"
            fill="currentColor"
            className="w-4 h-4 text-stone-500 dark:text-stone-300"
          >
            <path
              fillRule="evenodd"
              d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      )}

      {canRetryPendingSubmission && (
        <div className="mx-auto mb-2 flex max-w-4xl px-2">
          <button
            type="button"
            onClick={() => void onRetryPendingSubmission()}
            className="rounded-md border border-[var(--theme-border)] px-3 py-1.5 text-sm text-[var(--theme-text-secondary)] hover:bg-[var(--theme-bg-sidebar)]"
          >
            {t("chat.runTerminal.statusUnavailable", {
              defaultValue: t("chat.requestFailed"),
            })}
          </button>
        </div>
      )}
      <ChatInput
        {...chatInputProps}
        className="mx-auto max-w-4xl px-2"
      />
    </div>
  );

  return (
    <SessionImageGalleryProvider messages={displayMessages}>
      <WorkbenchShellComponent
        composer={messages.length > 0 ? composer : undefined}
        rightPanel={rightPanel}
      >
      <main
        ref={messagesContainerRef}
        className={`relative min-h-0 flex-1 bg-[var(--theme-workbench-canvas)] ${
          messages.length > 0 ? "overflow-hidden" : ""
        }`}
      >
        {messages.length === 0 ? (
          isLoading ? (
            <ChatSkeleton count={5} />
          ) : (
            <WelcomePage
              greeting={greeting}
              subtitle={
                t("chat.welcomeSubtitle") ?? "How can I help you today?"
              }
              composer={composer}
            />
          )
        ) : (
          <Virtuoso
            key={messageListSessionKey}
            ref={virtuosoRef}
            className="dark:divide-stone-800 overflow-x-hidden"
            data={displayMessages}
            computeItemKey={(_, message) => message.id}
            atBottomStateChange={handleVirtuosoAtBottomChange}
            atBottomThreshold={getAtBottomThresholdPx(isMobileViewport)}
            followOutput={"smooth"}
            rangeChanged={handleVirtuosoRangeChanged}
            components={virtuosoComponents}
            itemContent={virtuosoItemContent}
            initialTopMostItemIndex={getInitialBottomItemLocation(
              messages.length,
            )}
          />
        )}
      </main>

      <ApprovalPanel
        approvals={approvals}
        onRespond={onRespondApproval}
        isLoading={approvalLoading}
      />

      <RevealPreviewHost
        preview={activePreview}
        onClose={() => handleClosePreview(true)}
        onUserInteraction={handlePreviewInteraction}
      />
      <AttachmentPreviewHost />
      <PersistentToolPanelHost />
      </WorkbenchShellComponent>
    </SessionImageGalleryProvider>
  );
}
