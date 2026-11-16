import {
  useEffect,
  useState,
  useCallback,
  useRef,
  useLayoutEffect,
} from "react";
import { Users } from "lucide-react";
import { useTranslation } from "react-i18next";
import i18n from "../../../i18n";
import { Spin } from "@lobehub/ui/base-ui";
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
import { formatDateTime } from "../../../utils/datetime";
import { SubagentList } from "./elements/SubagentList";
import { TaskCard, type TaskCardStatus } from "./elements/TaskCard";

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
  const statusLabel = i18n.t(`assistantUi.subagent.status.${effectiveStatus}`);

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
    formattedAgentName: data.agentName === "Sub-agent" ? i18n.t("assistantUi.subagent.name") : formatSubagentName(data.agentName),
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

  openPersistentToolPanel({
    title: formattedAgentName,
    icon: <Users size={16} />,
    status: panelStatus,
    subtitle,
    panelKey,
    children: <SubagentPanelContent agentId={agentId} />,
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
          <div className="space-y-2 pl-3 border-l-2 border-neutral-200 dark:border-neutral-700">
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
          <div className="flex items-center gap-2 text-neutral-500 dark:text-neutral-400">
            <Spin size={16} />
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

    if (isPersistentToolPanelOpen(panelKey)) {
      updatePersistentToolPanel(
        (prev) => ({
          ...prev,
          status: panelStatus,
          subtitle,
        }),
        panelKey,
      );
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
    openPersistentToolPanel({
      title: formattedAgentName,
      icon: <Users size={16} />,
      status: panelStatus,
      subtitle,
      panelKey,
      children: <SubagentPanelContent agentId={agent_id} />,
    });
  }, [formattedAgentName, panelStatus, subtitle, panelKey, agent_id]);

  const taskStatus: TaskCardStatus = effectiveStatus;
  return (
    <TaskCard
      id={agent_id}
      parentId={parentAgentId}
      name={formattedAgentName}
      status={taskStatus}
      statusLabel={statusLabel}
      currentCategory={currentCategory}
      progressPercent={progressPercent}
      durationMs={durationMs}
      onOpen={handleOpenInPanel}
    >
      {inlineNestedEntries.length > 0 && (
        <SubagentList>
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
        </SubagentList>
      )}
    </TaskCard>
  );
}
