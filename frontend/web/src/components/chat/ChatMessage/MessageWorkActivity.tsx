import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { assistantTextPartRole } from "../../../types/assistantTextParts";
import type { ExecutionTimelinePart, MessagePart } from "../../../types/message";
import { isWorkActivityPart } from "./messagePartVisibility";
import { shouldExpandWorkActivity } from "./workActivityExpansion";
import { RunActivity } from "./elements/RunActivity";

export interface WorkActivityIssueCounts {
  failed: number;
  denied: number;
}

interface WorkActivityPartEntry {
  part: MessagePart;
  index: number;
}

interface WorkActivityToolGroup {
  kind: "tool_group";
  items: WorkActivityPartEntry[];
}

interface WorkActivitySinglePart {
  kind: "part";
  item: WorkActivityPartEntry;
}

type WorkActivityDisplayItem = WorkActivityToolGroup | WorkActivitySinglePart;

function isExecutionStepPart(
  part: MessagePart,
): part is Extract<MessagePart, { type: "execution_step" }> {
  return part.type === "execution_step";
}

function isExecutionProcessPart(
  part: MessagePart,
): part is Extract<MessagePart, { type: "execution_process" }> {
  return part.type === "execution_process";
}

const ROUTINE_EXECUTION_STAGES = new Set([
  "attachment_materialization",
  "artifact_recovery",
  "artifact_validation",
  "model_response",
  "model_wait",
  "sandbox_preparation",
  "sandbox_ready",
  "sandbox_submission",
  "skill_staging",
  "skills_ready",
]);

function executionStepsOf(part: MessagePart): readonly ExecutionTimelinePart[] {
  if (isExecutionStepPart(part)) return [part];
  if (isExecutionProcessPart(part)) return part.steps;
  return [];
}

function isRoutineExecutionStep(step: ExecutionTimelinePart): boolean {
  return Boolean(
    step.stage &&
      (ROUTINE_EXECUTION_STAGES.has(step.stage) ||
        step.stage.startsWith("sandbox_")),
  );
}

function isWorkNarrationPart(part: MessagePart): boolean {
  return (
    (part.type === "text" && assistantTextPartRole(part) === "work") ||
    (part.type === "summary" && part.kind === "work_trace")
  );
}

/** Keep public work and active tools while collapsing routine agent phases. */
function selectDisplayWorkParts(
  parts: readonly MessagePart[],
  isStreaming: boolean | undefined,
): WorkActivityPartEntry[] {
  let latestMeaningfulSequence = -1;
  parts.forEach((part) => {
    executionStepsOf(part).forEach((step) => {
      if (!isRoutineExecutionStep(step)) {
        latestMeaningfulSequence = Math.max(latestMeaningfulSequence, step.sequence);
      }
    });
  });

  const latestNarrationIndex = parts.reduce(
    (latest, part, index) => (isWorkNarrationPart(part) ? index : latest),
    -1,
  );
  const shouldDisplayStep = (step: ExecutionTimelinePart) =>
    step.status === "failed" ||
    (!isRoutineExecutionStep(step) &&
      (isStreaming !== true || step.sequence === latestMeaningfulSequence));
  const selected: WorkActivityPartEntry[] = [];

  parts.forEach((part, index) => {
    if (isExecutionStepPart(part)) {
      if (shouldDisplayStep(part)) selected.push({ part, index });
      return;
    }
    if (isExecutionProcessPart(part)) {
      const steps = part.steps.filter(shouldDisplayStep);
      if (steps.length > 0) {
        selected.push({
          index,
          part: steps.length === part.steps.length ? part : { ...part, steps },
        });
      }
      return;
    }
    if (isWorkNarrationPart(part) && isStreaming === true && index !== latestNarrationIndex) {
      return;
    }
    if (isWorkActivityPart(part)) selected.push({ part, index });
  });

  return selected;
}

function buildWorkActivityDisplayItems(
  entries: readonly WorkActivityPartEntry[],
): WorkActivityDisplayItem[] {
  const items: WorkActivityDisplayItem[] = [];
  let successfulTools: WorkActivityPartEntry[] = [];
  const flushSuccessfulTools = () => {
    if (successfulTools.length > 0) {
      items.push(
        successfulTools.length > 1
          ? { kind: "tool_group", items: successfulTools }
          : { kind: "part", item: successfulTools[0] },
      );
      successfulTools = [];
    }
  };

  entries.forEach((entry) => {
    const previousTool = successfulTools.at(-1);
    if (
      entry.part.type === "tool" &&
      entry.part.status === "completed" &&
      (previousTool === undefined || entry.index === previousTool.index + 1)
    ) {
      successfulTools.push(entry);
      return;
    }
    flushSuccessfulTools();
    items.push({ kind: "part", item: entry });
  });
  flushSuccessfulTools();
  return items;
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

  const displayEntries = selectDisplayWorkParts(parts, isStreaming);
  const displayItems = buildWorkActivityDisplayItems(displayEntries);
  const workActivityCount = displayItems.length;
  const issueCounts = countWorkActivityIssues(parts);
  const workActivityRegionIds = displayItems.map((item) => {
    const entries = item.kind === "tool_group" ? item.items : [item.item];
    return `chat-work-${messageId}-${entries.map(({ index }) => index).join("-")}`;
  });
  const controlledWorkActivityIds = workActivityRegionIds.join(" ");
  const activityStatus = isStreaming
    ? "running"
    : issueCounts.failed > 0 || issueCounts.denied > 0
      ? "error"
      : "complete";

  const renderResultPart = (part: MessagePart, index: number) => (
    <Fragment key={partKeys[index] ?? `${messageId}:${index}`}>
      {renderPart(part, index, false)}
    </Fragment>
  );
  const renderWorkEntry = ({ part, index }: WorkActivityPartEntry) => (
    <Fragment key={partKeys[index] ?? `${messageId}:${index}`}>
      {renderPart(part, index, true)}
    </Fragment>
  );
  const renderDisplayItem = (item: WorkActivityDisplayItem) => {
    if (item.kind === "part") return renderWorkEntry(item.item);
    return (
      <details data-tool-group className="my-1 rounded-lg border border-[var(--theme-border)]">
        <summary className="cursor-pointer list-none px-3 py-2 text-sm font-medium marker:content-none">
          {t("chat.workDetails.toolGroup", { count: item.items.length })}
        </summary>
        <div className="border-t border-[var(--theme-border)] px-2 py-1">
          {item.items.map(renderWorkEntry)}
        </div>
      </details>
    );
  };
  if (displayItems.length === 0) {
    return (
      <div data-message-work-activity-order="result-first">
        {parts.map((part, index) =>
          isWorkActivityPart(part) ? null : renderResultPart(part, index),
        )}
      </div>
    );
  }
  // The visible result reads first; the process disclosure follows it so the
  // user opens an execution trace only after reading the answer.
  return (
    <div data-message-work-activity-order="result-first">
      {parts.map((part, index) =>
        isWorkActivityPart(part) ? null : renderResultPart(part, index),
      )}
      {displayItems.map((item, displayIndex) => {
        const isFirstWorkActivity = displayIndex === 0;
        const hasIssues = issueCounts.failed > 0 || issueCounts.denied > 0;
        return (
          <Fragment key={workActivityRegionIds[displayIndex]}>
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
              contentId={workActivityRegionIds[displayIndex]}
              ariaControls={controlledWorkActivityIds}
              showTrigger={isFirstWorkActivity}
              issueSummary={
                isFirstWorkActivity && hasIssues ? (
                  <span className="inline-flex flex-wrap items-center gap-2 text-xs font-normal text-[var(--theme-warning)]" data-work-activity-issue-summary>
                    {issueCounts.failed > 0 ? <span data-work-activity-failed-count>局部失败 {issueCounts.failed}</span> : null}
                    {issueCounts.denied > 0 ? <span data-work-activity-denied-count>未授权 {issueCounts.denied}</span> : null}
                    <span>查看执行详情</span>
                  </span>
                ) : undefined
              }
            >
              {renderDisplayItem(item)}
            </RunActivity>
          </Fragment>
        );
      })}
    </div>
  );
}
