import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { MessagePart } from "../../../types";
import { isWorkActivityPart } from "./messagePartVisibility";
import { shouldExpandWorkActivity } from "./workActivityExpansion";
import { RunActivity } from "./elements/RunActivity";

export interface WorkActivityIssueCounts {
  failed: number;
  denied: number;
}

/** Count local operation problems without changing the enclosing Run status. */
function countWorkActivityIssues(parts: readonly MessagePart[]): WorkActivityIssueCounts {
  const counts: WorkActivityIssueCounts = { failed: 0, denied: 0 };
  for (const part of parts) {
    if (part.type === "tool") {
      if (part.status === "failed") counts.failed += 1;
      if (part.status === "denied") counts.denied += 1;
    } else if (part.type === "subagent") {
      if (part.status === "error") counts.failed += 1;
      if (part.parts) {
        const nested = countWorkActivityIssues(part.parts);
        counts.failed += nested.failed;
        counts.denied += nested.denied;
      }
    } else if (part.type === "execution_step") {
      if (part.status === "failed") counts.failed += 1;
    } else if (part.type === "execution_process") {
      counts.failed += part.steps.filter((step) => step.status === "failed").length;
    }
  }
  return counts;
}

export function MessageWorkActivity({
  messageId,
  isStreaming,
  parts,
  partKeys,
  renderPart,
}: {
  messageId: string;
  isStreaming?: boolean;
  parts: MessagePart[];
  partKeys: string[];
  renderPart: (
    part: MessagePart,
    index: number,
    withinWorkDetails: boolean,
  ) => ReactNode;
}) {
  const { t } = useTranslation();
  const autoExpanded = shouldExpandWorkActivity(parts, isStreaming, isWorkActivityPart);
  const [expanded, setExpanded] = useState(autoExpanded);
  const previousPhaseRef = useRef({ messageId, autoExpanded });

  useEffect(() => {
    const previous = previousPhaseRef.current;
    if (previous.messageId !== messageId || previous.autoExpanded !== autoExpanded) {
      setExpanded(autoExpanded);
    }
    previousPhaseRef.current = { messageId, autoExpanded };
  }, [messageId, autoExpanded]);

  const workActivityCount = parts.filter(isWorkActivityPart).length;
  const issueCounts = countWorkActivityIssues(parts);
  const firstWorkActivityIndex = parts.findIndex(isWorkActivityPart);
  const workActivityRegionIds = parts.map((part, index) =>
    isWorkActivityPart(part) ? `chat-work-${messageId}-${index}` : "",
  );
  const controlledWorkActivityIds = workActivityRegionIds.filter(Boolean).join(" ");
  const activityStatus = isStreaming
    ? "running"
    : issueCounts.failed > 0 || issueCounts.denied > 0
      ? "error"
      : "complete";

  return parts.map((part, index) => {
    const isWorkActivity = isWorkActivityPart(part);
    const renderedPart = renderPart(part, index, isWorkActivity);
    return (
      <Fragment key={partKeys[index] ?? `${messageId}:${index}`}>
        {isWorkActivity ? (
          <RunActivity
            open={expanded}
            onOpenChange={setExpanded}
            status={activityStatus}
            label={t(isStreaming ? "chat.workDetails.working" : "chat.workDetails.title")}
            itemCount={t("chat.workDetails.itemCount", { count: workActivityCount })}
            toggleLabel={t(
              expanded
                ? "chat.workDetails.collapseAll"
                : "chat.workDetails.expandAll",
            )}
            contentId={workActivityRegionIds[index]}
            ariaControls={controlledWorkActivityIds}
            showTrigger={index === firstWorkActivityIndex}
            issueSummary={
              index === firstWorkActivityIndex && (issueCounts.failed > 0 || issueCounts.denied > 0) ? (
                <span className="inline-flex flex-wrap items-center gap-2 text-xs font-normal text-[var(--theme-warning)]" data-work-activity-issue-summary>
                  {issueCounts.failed > 0 ? <span data-work-activity-failed-count>局部失败 {issueCounts.failed}</span> : null}
                  {issueCounts.denied > 0 ? <span data-work-activity-denied-count>未授权 {issueCounts.denied}</span> : null}
                  <span>查看执行详情</span>
                </span>
              ) : undefined
            }
          >
            {renderedPart}
          </RunActivity>
        ) : (
          renderedPart
        )}
      </Fragment>
    );
  });
}
