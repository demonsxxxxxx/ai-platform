import { FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import { MarkdownContent } from "./MarkdownContent";

export function SummaryItem({
  content,
  isStreaming,
}: {
  content: string;
  isStreaming?: boolean;
}) {
  const { t } = useTranslation();

  return (
    <div
      data-message-commentary
      className="my-2 flex min-w-0 items-start gap-2 border-l-2 border-border/70 pl-3 text-sm text-muted-foreground"
    >
      <FileText size={14} className="mt-1 shrink-0 opacity-60" />
      <div className="min-w-0 flex-1">
        <div className="mb-1 text-xs font-medium opacity-70">
          {t("chat.message.summary")}
        </div>
        <MarkdownContent content={content} isStreaming={isStreaming} />
      </div>
    </div>
  );
}
