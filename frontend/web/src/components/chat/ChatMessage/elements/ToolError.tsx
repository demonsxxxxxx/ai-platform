import { Ban, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";

export type ToolErrorKind = "failed" | "denied" | "cancelled";

const LABELS: Record<ToolErrorKind, string> = {
  failed: "assistantUi.toolError.failed",
  denied: "assistantUi.toolError.denied",
  cancelled: "assistantUi.toolError.cancelled",
};

/** Fixed public error state; raw tool errors never reach this element. */
export function ToolError({ kind }: { kind: ToolErrorKind }) {
  const { t } = useTranslation();
  return (
    <span
      data-slot="tool-error"
      data-kind={kind}
      aria-label={t(LABELS[kind])}
      className="inline-flex items-center gap-1 text-xs font-medium"
    >
      {kind === "failed" ? <XCircle size={12} aria-hidden="true" /> : <Ban size={12} aria-hidden="true" />}
      <span>{t(LABELS[kind])}</span>
    </span>
  );
}
