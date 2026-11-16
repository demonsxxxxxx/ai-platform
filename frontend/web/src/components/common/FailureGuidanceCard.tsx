import { Button } from "@lobehub/ui/base-ui";
import { Check, Copy } from "lucide-react";
import { useState } from "react";
import type { FailureGuidance } from "../../types/failureGuidance";

export function FailureGuidanceCard({
  guidance,
  className = "",
}: {
  guidance: FailureGuidance;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const rows = [
    ["发生了什么", guidance.whatHappened],
    ["已经完成并保留了什么", guidance.retained],
    ["现在可以做什么", guidance.nextAction],
  ] as const;

  const copyProblemNumber = async () => {
    if (!guidance.problemNumber || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(guidance.problemNumber);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1_500);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section
      className={`rounded-lg border border-[var(--theme-warning-ring)] bg-[var(--theme-warning-soft)] p-3 text-xs ${className}`}
      data-failure-guidance
    >
      <dl className="space-y-2.5">
        {rows.map(([label, value]) => (
          <div key={label}>
            <dt className="font-semibold text-neutral-800 dark:text-neutral-100">{label}</dt>
            <dd className="mt-0.5 leading-5 text-neutral-700 dark:text-neutral-300">{value}</dd>
          </div>
        ))}
        <div>
          <dt className="font-semibold text-neutral-800 dark:text-neutral-100">问题编号</dt>
          <dd className="mt-1 flex min-w-0 items-center gap-2">
            <code
              className="min-w-0 truncate rounded bg-[color-mix(in_srgb,var(--theme-bg-card)_70%,transparent)] px-1.5 py-1 text-[11px] text-neutral-700 dark:bg-neutral-900/70 dark:text-neutral-200"
              title={guidance.problemNumber ?? undefined}
            >
              {guidance.problemNumber ?? "尚未生成"}
            </code>
            {guidance.problemNumber ? (
              <Button type="text"
                htmlType="button"
                onClick={() => void copyProblemNumber()}
                className="inline-flex shrink-0 items-center gap-1 rounded border border-[var(--theme-warning-ring)] bg-[color-mix(in_srgb,var(--theme-bg-card)_80%,transparent)] px-2 py-1 text-[11px] font-medium text-neutral-700 hover:bg-[var(--theme-bg-card)] dark:bg-neutral-900/80 dark:text-neutral-200"
                aria-label="复制问题编号"
              >
                {copied ? <Check size={12} /> : <Copy size={12} />}
                {copied ? "已复制" : "复制"}
              </Button>
            ) : null}
          </dd>
        </div>
      </dl>
    </section>
  );
}
