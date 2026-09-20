import {
  useEffect,
  useState,
  useCallback,
  useRef,
  useLayoutEffect,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { clsx } from "clsx";
import {
  CheckCircle,
  XCircle,
  Ban,
  ChevronRight,
  Users,
  Loader2,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { LoadingSpinner } from "../../common";
import type { CollapsibleStatus } from "../../common";
import type { MessagePart } from "../../../types";
import {
  createMessagePartRenderKeys,
  MessagePartRenderer,
} from "./MessagePartRenderer";
import {
  createSubagentArtifactDownloadScope,
  type ArtifactDownloadScope,
} from "./items/artifactDownloadRegistry";
import {
  createSubagentAnchorOwnerId,
  createSubagentPanelKey,
} from "./messagePartAnchors";
import {
  openPersistentToolPanel,
  updatePersistentToolPanel,
  isPersistentToolPanelOpen,
} from "./items/persistentToolPanelState";
import {
  subagentPanelStore,
  type SubagentPanelData,
} from "./subagentPanelStore";
import {
  isNearSubagentPanelBottom,
  shouldAutoScrollSubagentPanel,
} from "./subagentPanelScroll";
import {
  dismissSubagentPanelAutoOpen,
  isSubagentPanelAutoOpenDismissed,
  resetSubagentPanelAutoOpenDismissal,
  shouldAutoOpenSubagentPanel,
} from "./subagentPanelControl";
import { formatDateTime } from "../../../utils/datetime";

function useSubagentPanelData(agentId: string): SubagentPanelData | undefined {
  const [, forceRender] = useState(0);

  useEffect(() => {
    const listener = () => forceRender((n) => n + 1);
    return subagentPanelStore.subscribe(agentId, listener);
  }, [agentId]);

  return subagentPanelStore.get(agentId);
}

function formatSubagentName(agentName: string): string {
  return agentName
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(" ");
}

// eslint-disable-next-line react-refresh/only-export-components
export function buildSubagentPanelState(data: SubagentPanelData) {
  const effectiveStatus = data.status || (data.isPending ? "running" : "pending");
  const panelStatus: CollapsibleStatus =
    effectiveStatus === "running"
      ? "loading"
      : effectiveStatus === "complete"
        ? "success"
        : effectiveStatus === "error"
          ? "error"
          : effectiveStatus === "cancelled"
            ? "cancelled"
            : "idle";
  const subtitle = data.startedAt ? formatDateTime(data.startedAt) : undefined;
  const statusLabel =
    effectiveStatus === "running"
      ? "Running"
      : effectiveStatus === "complete"
        ? "Completed"
        : effectiveStatus === "error"
          ? "Failed"
          : effectiveStatus === "cancelled"
            ? "Cancelled"
            : "Pending";

  return {
    effectiveStatus,
    panelStatus,
    subtitle: subtitle || undefined,
    statusLabel,
    parentAgentId: data.parentAgentId,
    durationMs: data.durationMs,
    progressPercent: data.progressPercent,
    currentCategory: data.currentCategory,
    panelKey: createSubagentPanelKey(data.agentId),
    formattedAgentName: formatSubagentName(data.agentName),
  };
}

// eslint-disable-next-line react-refresh/only-export-components -- exercised by subagent reconciliation coverage.
export function createSubagentPartRenderKeys(
  agentId: string,
  parts: MessagePart[],
) {
  return createMessagePartRenderKeys(createSubagentAnchorOwnerId(agentId), parts);
}

// eslint-disable-next-line react-refresh/only-export-components -- exercised by subagent reconciliation coverage.
export function createSubagentPartRenderEntries(
  agentId: string,
  parts: MessagePart[],
  target: "panel" | "nested",
) {
  const keys = createSubagentPartRenderKeys(agentId, parts);
  return parts
    .map((part, index) => ({ part, index, key: keys[index] }))
    .filter(({ part }) =>
      target === "nested" ? part.type === "subagent" : part.type !== "subagent",
    );
}

// eslint-disable-next-line react-refresh/only-export-components -- exercised by panel-state coverage.
export function shouldShowSubagentPanelLoading(
  isPending: boolean | undefined,
  panelEntryCount: number,
): boolean {
  return isPending === true && panelEntryCount === 0;
}

// eslint-disable-next-line react-refresh/only-export-components
export function openSubagentPanelByAgentId(agentId: string): boolean {
  const data = subagentPanelStore.get(agentId);
  if (!data) {
    return false;
  }

  const { panelStatus, subtitle, panelKey, formattedAgentName } =
    buildSubagentPanelState(data);

  if (isPersistentToolPanelOpen(panelKey)) {
    return true;
  }

  resetSubagentPanelAutoOpenDismissal();
  openPersistentToolPanel({
    title: formattedAgentName,
    icon: <Users size={16} />,
    status: panelStatus,
    subtitle,
    panelKey,
    children: <SubagentPanelContent agentId={agentId} />,
    onUserClose: dismissSubagentPanelAutoOpen,
  });

  return true;
}

// ==========================================
// Subagent panel content (reactive)
// ==========================================

function SubagentPanelContent({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const data = useSubagentPanelData(agentId);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const userScrolledUpRef = useRef(false);

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, []);

  const handleScroll = useCallback(() => {
    const scroller = scrollRef.current;
    if (!scroller) return;
    userScrolledUpRef.current = !isNearSubagentPanelBottom(scroller);
  }, []);

  useLayoutEffect(() => {
    if (
      !shouldAutoScrollSubagentPanel({
        scroller: scrollRef.current,
        userScrolledUp: userScrolledUpRef.current,
      })
    ) {
      return;
    }

    scrollToBottom();
  });

  useEffect(() => {
    const scroller = scrollRef.current;
    if (!scroller || typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver(() => {
      if (
        shouldAutoScrollSubagentPanel({
          scroller,
          userScrolledUp: userScrolledUpRef.current,
        })
      ) {
        scrollToBottom();
      }
    });

    observer.observe(scroller);
    if (contentRef.current) {
      observer.observe(contentRef.current);
    }

    return () => observer.disconnect();
  }, [scrollToBottom]);

  if (!data) return null;

  const nestedArtifactDownloadScope = createSubagentArtifactDownloadScope(
    data.artifactDownloadScope,
    agentId,
  );
  const panelPartEntries = createSubagentPartRenderEntries(
    agentId,
    data.parts || [],
    "panel",
  );

  return (
    <div
      ref={scrollRef}
      onScroll={handleScroll}
      className="max-h-full overflow-y-auto p-2 sm:p-4"
    >
      <div ref={contentRef} className="space-y-3">
        {panelPartEntries.length > 0 && (
          <div className="space-y-2 pl-3 border-l-2 border-stone-200 dark:border-stone-700">
            {panelPartEntries.map(({ part, index, key }, entryIndex) => (
              <MessagePartRenderer
                key={key}
                part={part}
                messageId={createSubagentAnchorOwnerId(agentId)}
                partIndex={index}
                isStreaming={data.isPending}
                isLast={entryIndex === panelPartEntries.length - 1}
                artifactDownloadScope={nestedArtifactDownloadScope}
              />
            ))}
          </div>
        )}
        {shouldShowSubagentPanelLoading(
          data.isPending,
          panelPartEntries.length,
        ) && (
          <div className="flex items-center gap-2 text-stone-500 dark:text-stone-400">
            <LoadingSpinner size="sm" />
            <span className="text-sm">{t("chat.message.executing")}</span>
          </div>
        )}
        <div ref={bottomRef} className="h-px" />
      </div>
    </div>
  );
}

// ==========================================
// Utility
// ==========================================

// Subagent Block - compact card, content always in sidebar panel
export function SubagentBlock({
  agent_id,
  agent_name,
  isPending,
  parts,
  startedAt,
  completedAt,
  status,
  artifactDownloadScope,
  parent_agent_id,
  duration_ms,
  progress_percent,
  current_category,
}: {
  agent_id: string;
  agent_name: string;
  isPending?: boolean;
  parts?: MessagePart[];
  startedAt?: number;
  completedAt?: number;
  status?: "pending" | "running" | "complete" | "error" | "cancelled";
  artifactDownloadScope?: ArtifactDownloadScope;
  parent_agent_id?: string;
  duration_ms?: number;
  progress_percent?: number;
  current_category?: string;
}) {
  const {
    effectiveStatus,
    panelStatus,
    subtitle,
    statusLabel,
    panelKey,
    formattedAgentName,
    parentAgentId,
    durationMs,
    progressPercent,
    currentCategory,
  } = buildSubagentPanelState({
    agentId: agent_id,
    agentName: agent_name,
    artifactDownloadScope,
    isPending,
    parts,
    startedAt,
    completedAt,
    status,
    currentCategory: current_category,
    parentAgentId: parent_agent_id,
    durationMs: duration_ms,
    progressPercent: progress_percent,
  });
  const inlineNestedEntries = createSubagentPartRenderEntries(
    agent_id,
    parts || [],
    "nested",
  );
  const inlineNestedArtifactScope = createSubagentArtifactDownloadScope(
    artifactDownloadScope,
    agent_id,
  );
  // Keep sidebar panel data in sync
  useEffect(() => {
    subagentPanelStore.set({
      agentId: agent_id,
      agentName: agent_name,
      artifactDownloadScope,
      isPending,
      parts,
      startedAt,
      completedAt,
      status: effectiveStatus as SubagentPanelData["status"],
      parentAgentId: parent_agent_id,
      durationMs: duration_ms,
      progressPercent: progress_percent,
      currentCategory: current_category,
    });

    // Auto-open only when no panel is open; multiple running subagents should not steal focus.
    if (isPersistentToolPanelOpen(panelKey)) {
      updatePersistentToolPanel(
        (prev) => ({
          ...prev,
          status: panelStatus,
          subtitle,
        }),
        panelKey,
      );
    } else if (
      shouldAutoOpenSubagentPanel({
        status: effectiveStatus,
        anyPanelOpen: isPersistentToolPanelOpen(),
        autoOpenDismissed: isSubagentPanelAutoOpenDismissed(),
      })
    ) {
      openPersistentToolPanel({
        title: formattedAgentName,
        icon: <Users size={16} />,
        status: panelStatus,
        subtitle,
        panelKey,
        children: <SubagentPanelContent agentId={agent_id} />,
        auto: true,
        onUserClose: dismissSubagentPanelAutoOpen,
      });
    }
  }, [
    agent_id,
    agent_name,
    artifactDownloadScope,
    isPending,
    parts,
    startedAt,
    completedAt,
    effectiveStatus,
    panelStatus,
    subtitle,
    formattedAgentName,
    panelKey,
    parent_agent_id,
    duration_ms,
    progress_percent,
    current_category,
  ]);

  useEffect(() => {
    return () => {
      subagentPanelStore.delete(agent_id);
    };
  }, [agent_id]);

  const handleOpenInPanel = useCallback(() => {
    resetSubagentPanelAutoOpenDismissal();
    openPersistentToolPanel({
      title: formattedAgentName,
      icon: <Users size={16} />,
      status: panelStatus,
      subtitle,
      panelKey,
      children: <SubagentPanelContent agentId={agent_id} />,
      onUserClose: dismissSubagentPanelAutoOpen,
    });
  }, [formattedAgentName, panelStatus, subtitle, panelKey, agent_id]);

  const handleTriggerKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLButtonElement>) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      // Prevent native click synthesis so explicit keyboard support cannot
      // double-open the panel when the browser also dispatches activation.
      event.preventDefault();
      handleOpenInPanel();
    },
    [handleOpenInPanel],
  );

  return (
    <div
      data-subagent-id={agent_id}
      data-parent-agent-id={parentAgentId}
      className={clsx(
        "my-1.5 rounded-xl overflow-hidden min-w-0 group",
        "border transition-all duration-200",
        parentAgentId &&
          "ml-4 border-l-2 border-l-stone-300 pl-2 dark:border-l-stone-600",
        effectiveStatus === "running" &&
          "border-stone-200/60 dark:border-stone-700/40 bg-stone-50/50 dark:bg-stone-800/30",
        effectiveStatus === "complete" &&
          "border-stone-200/60 dark:border-stone-700/40 bg-stone-50/50 dark:bg-stone-800/30",
        effectiveStatus === "error" &&
          "border-red-200/60 dark:border-red-900/40 bg-gradient-to-r from-red-50/60 to-transparent dark:from-red-950/20",
        effectiveStatus === "cancelled" &&
          "border-stone-200/60 dark:border-stone-700/40 bg-stone-50/50 dark:bg-stone-800/30",
        (!effectiveStatus || effectiveStatus === "pending") &&
          "border-stone-200/60 dark:border-stone-700/40",
      )}
    >
      <button
        type="button"
        aria-label={`${formattedAgentName}: ${statusLabel}${parentAgentId ? ", Nested Agent" : ""}`}
        aria-haspopup="dialog"
        data-subagent-trigger={agent_id}
        className="flex w-full items-center gap-3 px-3.5 py-2.5 text-left cursor-pointer transition-colors hover:bg-white/60 dark:hover:bg-white/5"
        onClick={handleOpenInPanel}
        onKeyDown={handleTriggerKeyDown}
      >
        <div
          className={clsx(
            "flex h-7 w-7 items-center justify-center rounded-lg shrink-0",
            effectiveStatus === "running" && "bg-amber-500/10",
            effectiveStatus === "complete" && "bg-emerald-500/10",
            effectiveStatus === "error" && "bg-red-500/10",
            effectiveStatus === "cancelled" && "bg-amber-500/10",
            (!effectiveStatus || effectiveStatus === "pending") &&
              "bg-stone-500/10",
          )}
        >
          {effectiveStatus === "running" ? (
            <Loader2
              size={13}
              className="text-amber-500 dark:text-amber-400 animate-spin"
            />
          ) : effectiveStatus === "complete" ? (
            <CheckCircle
              size={13}
              className="text-emerald-500 dark:text-emerald-400"
            />
          ) : effectiveStatus === "error" ? (
            <XCircle size={13} className="text-red-500 dark:text-red-400" />
          ) : effectiveStatus === "cancelled" ? (
            <Ban size={13} className="text-amber-500 dark:text-amber-400" />
          ) : (
            <ChevronRight
              size={13}
              className="text-stone-400 dark:text-stone-500"
            />
          )}
        </div>

        <div className="flex-1 min-w-0">
          <span
            className={clsx(
              "text-[13px] font-medium truncate block",
              effectiveStatus === "running" &&
                "text-stone-700 dark:text-stone-300",
              effectiveStatus === "complete" &&
                "text-stone-700 dark:text-stone-300",
              effectiveStatus === "error" && "text-red-700 dark:text-red-300",
              effectiveStatus === "cancelled" &&
                "text-stone-700 dark:text-stone-300",
              (!effectiveStatus || effectiveStatus === "pending") &&
                "text-stone-600 dark:text-stone-400",
            )}
          >
            {formattedAgentName}
          </span>
          <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[10px] text-stone-500 dark:text-stone-400">
            <span data-subagent-status>{statusLabel}</span>
            {parentAgentId && <span data-subagent-nested-label>Nested Agent</span>}
            {currentCategory && <span>Category: {currentCategory}</span>}
            {progressPercent !== undefined && <span>Progress: {progressPercent}%</span>}
            {durationMs !== undefined && <span>Duration: {(durationMs / 1000).toFixed(1)}s</span>}
          </div>
        </div>
      </button>
      {inlineNestedEntries.length > 0 && (
        <div data-subagent-children className="ml-4 border-l-2 border-stone-200/60 pl-2 dark:border-stone-700/50">
          {inlineNestedEntries.map(({ part, index, key }, entryIndex) => (
            <MessagePartRenderer
              key={key}
              part={part}
              messageId={createSubagentAnchorOwnerId(agent_id)}
              partIndex={index}
              isStreaming={isPending}
              isLast={entryIndex === inlineNestedEntries.length - 1}
              artifactDownloadScope={inlineNestedArtifactScope}
            />
          ))}
        </div>
      )}
    </div>
  );
}
