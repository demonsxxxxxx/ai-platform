import { Fragment, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ExecutionTimelinePart, MessagePart } from "../../../types/message";
import { isWorkActivityPart } from "./messagePartVisibility";
import { shouldExpandWorkActivity } from "./workActivityExpansion";
import { RunActivity } from "./elements/RunActivity";
import { formatTaskDuration } from "./taskDuration";

export interface WorkActivityIssueCounts {
  failed: number;
  denied: number;
}

interface WorkActivityPartEntry {
  part: MessagePart;
  index: number;
}


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

    if (isWorkActivityPart(part)) selected.push({ part, index });
  });

  return selected;
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

function WorkActivityDisclosure({
  messageId, entries, isStreaming, duration, children,
}: { messageId: string; entries: WorkActivityPartEntry[]; isStreaming?: boolean; duration?: ReactNode; children: ReactNode }) {
  const { t } = useTranslation();
  // The one message-level disclosure retains manual choice as operations arrive.
  const [expanded, setExpanded] = useState(() => shouldExpandWorkActivity(isStreaming));
  const issues = countWorkActivityIssues(entries.map(({ part }) => part));
  const hasIssues = issues.failed > 0 || issues.denied > 0;
  return <RunActivity
    open={expanded}
    onOpenChange={setExpanded}
    status={hasIssues ? "error" : isStreaming ? "running" : "complete"}
    label={t(isStreaming ? "chat.workDetails.working" : "chat.workDetails.title")}
    itemCount={t("chat.workDetails.itemCount", { count: entries.filter(({ part }) => isWorkActivityPart(part)).length })}
    toggleLabel={t(expanded ? "chat.workDetails.collapseAll" : "chat.workDetails.expandAll")}
    contentId={`chat-work-${messageId}`}
    duration={duration}
    issueSummary={hasIssues ? <span className="inline-flex flex-wrap items-center gap-x-1.5" data-work-activity-issue-summary>
      {issues.failed > 0 ? <span className="text-[var(--theme-danger-text)]" data-work-activity-failed-count>· {issues.failed} 项失败</span> : null}
      {issues.denied > 0 ? <span className="text-[var(--theme-warning-text)]" data-work-activity-denied-count>· {issues.denied} 项未授权</span> : null}
    </span> : undefined}
  >{children}</RunActivity>;
}

export function MessageWorkActivity({
  messageId, isStreaming, runDurationMs, parts, partKeys, renderPart,
}: {
  messageId: string;
  isStreaming?: boolean;
  runDurationMs?: number;
  parts: MessagePart[];
  partKeys: string[];
  renderPart: (part: MessagePart, index: number, withinWorkDetails: boolean) => ReactNode;
}) {
  const { t } = useTranslation();
  const formattedDuration = isStreaming === true ? null : formatTaskDuration(runDurationMs);
  const duration = formattedDuration !== null
    ? <span data-task-duration className="tabular-nums">
      {t("chat.message.duration")} {formattedDuration}
    </span> : undefined;
  const displayEntries = new Map(selectDisplayWorkParts(parts, isStreaming).map(entry => [entry.index, entry]));
  // Actual tool/subagent events establish the process boundary. Text is never
  // classified by wording: a later operation archives the preceding narration;
  // text after the last operation remains the live result until another starts.
  let lastOperation = -1;
  parts.forEach((part, index) => {
    if (part.type === "tool" || part.type === "subagent") lastOperation = index;
  });
  const process: WorkActivityPartEntry[] = [];
  const result: WorkActivityPartEntry[] = [];
  parts.forEach((part, index) => {
    if (isWorkActivityPart(part)) {
      const entry = displayEntries.get(index);
      if (entry) process.push(entry);
    } else if (part.type === "text" && index < lastOperation) {
      process.push({ part, index });
    } else {
      result.push({ part, index });
    }
  });
  const renderEntry = ({ part, index }: WorkActivityPartEntry, withinWorkDetails: boolean) => (
    <Fragment key={partKeys[index] ?? `${messageId}:${index}`}>
      {renderPart(part, index, withinWorkDetails)}
    </Fragment>
  );
  return <div data-message-work-activity-order="process-then-result">
    {process.length > 0 ? <WorkActivityDisclosure key="process" messageId={messageId} entries={process} isStreaming={isStreaming} duration={duration}>
      {process.map(entry => renderEntry(entry, true))}
    </WorkActivityDisclosure> : duration ? <div className="py-1 text-sm text-[var(--theme-text-secondary)]">{duration}</div> : null}
    <div data-message-result>{result.map(entry => renderEntry(entry, false))}</div>
  </div>;
}
