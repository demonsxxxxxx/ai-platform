import { CheckCircle, ChevronDown, LoaderCircle, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ExecutionTimelinePart } from "../../../types/message";
import { clsx } from "clsx";

function safePublicBasename(value: string | null): string | null {
  if (
    typeof value !== "string" ||
    value.length === 0 ||
    value.length > 128 ||
    value !== value.trim() ||
    value === "." ||
    value === ".." ||
    /[\\/:*?"<>|]/.test(value) ||
    [...value].some((character) => character.charCodeAt(0) < 32)
  ) {
    return null;
  }
  return value;
}

function hasPublicProgress(step: ExecutionTimelinePart): boolean {
  return (
    Number.isInteger(step.progress.current) &&
    Number.isInteger(step.progress.total) &&
    step.progress.total > 1 &&
    step.progress.current >= 0 &&
    step.progress.current <= step.progress.total
  );
}

const PRESENTATION_TITLES: Readonly<Record<string, string>> = {
  skill: "使用 Skill",
  mcp: "调用已授权工具",
  read: "读取和搜索文件",
  processing: "数据处理",
  write: "写入和编辑内容",
  agent: "协调任务",
  artifact: "生成交付物",
  verification: "校验结果",
  adjustment: "调整处理",
};

function StepRow({
  step,
  isStreaming,
}: {
  step: ExecutionTimelinePart;
  isStreaming: boolean;
}) {
  const { t } = useTranslation();
  const progress = hasPublicProgress(step);
  const kindLabel =
    step.title ||
    (step.presentation_kind
      ? PRESENTATION_TITLES[step.presentation_kind]
      : undefined) ||
    t(`chat.executionTimeline.kind.${step.kind}`);
  const statusLabel = t(`chat.executionTimeline.status.${step.status}`);
  const safeFileName = safePublicBasename(step.safe_file_name);
  const Icon =
    step.status === "failed"
      ? XCircle
      : step.status === "completed"
        ? CheckCircle
        : LoaderCircle;
  const tone =
    step.status === "failed"
      ? "text-red-700 dark:text-red-300"
      : step.status === "completed"
        ? "text-emerald-800 dark:text-emerald-200"
        : "text-[var(--theme-text-secondary)]";

  return (
    <li
      role={step.status === "failed" ? "alert" : "status"}
      className={clsx("flex min-w-0 items-start gap-2.5 py-1.5 text-sm", tone)}
    >
      <Icon
        size={16}
        className={clsx(
          "mt-0.5 shrink-0",
          isStreaming && step.status === "running" && "animate-spin",
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="break-words font-medium leading-snug">{kindLabel}</div>
        <div className="mt-0.5 text-xs opacity-80">
          {step.summary || statusLabel}
          {step.summary && ` · ${statusLabel}`}
          {progress &&
            ` · ${t("chat.executionTimeline.progress", {
              current: step.progress.current,
              total: step.progress.total,
            })}`}
        </div>
        {safeFileName && (
          <div className="mt-1 truncate text-xs opacity-70">{safeFileName}</div>
        )}
      </div>
    </li>
  );
}

/** Renders only versioned allowlisted public execution process fields. */
export function PublicExecutionProcess({
  steps,
  isStreaming,
  expandable = true,
}: {
  steps: ExecutionTimelinePart[];
  isStreaming: boolean;
  expandable?: boolean;
}) {
  const { t } = useTranslation();
  if (steps.length === 0) return null;

  const completed = steps.filter((step) => step.status === "completed").length;
  const failed = steps.filter((step) => step.status === "failed").length;
  const summary = t("chat.publicExecutionProcess.summary", {
    completed,
    failed,
    total: steps.length,
  });
  const rows = steps.map((step) => (
    <StepRow key={step.step_id} step={step} isStreaming={isStreaming} />
  ));

  if (!expandable) {
    return (
      <div data-public-execution-process className="my-1.5 max-w-xl">
        <ol className="list-none">{rows}</ol>
      </div>
    );
  }

  return (
    <details
      data-public-execution-process
      className="my-2 max-w-xl rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] text-[var(--theme-text)]"
    >
      <summary className="group flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm font-medium marker:content-none">
        <ChevronDown size={16} className="shrink-0 transition-transform group-open:rotate-180" />
        <span>{t("chat.publicExecutionProcess.title")}</span>
        <span className="ml-auto text-xs font-normal text-[var(--theme-text-secondary)]">
          {summary}
        </span>
      </summary>
      <ol className="border-t border-[var(--theme-border)] px-3 py-1.5">{rows}</ol>
    </details>
  );
}
