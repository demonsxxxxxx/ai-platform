import { Button } from "@lobehub/ui/base-ui";
import { memo, useState, useMemo } from "react";
import { Code, Eye } from "lucide-react";
import { DeferredCodeMirrorViewer } from "../../common/DeferredCodeMirrorViewer";
import { prepareHtmlPreviewContent } from "./htmlPreviewContent";
import { useTranslation } from "react-i18next";

interface HtmlPreviewProps {
  content: string; // HTML content directly
}

const HtmlPreview = memo(function HtmlPreview({ content }: HtmlPreviewProps) {
  const { t } = useTranslation();
  const [showSource, setShowSource] = useState(false);
  const previewContent = useMemo(
    () => prepareHtmlPreviewContent(content),
    [content],
  );

  return (
    <div className="h-full w-full flex flex-col bg-white dark:bg-neutral-900">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-neutral-50 dark:bg-neutral-900 border-b border-neutral-200 dark:border-neutral-700 shrink-0">
        <div className="flex items-center gap-2">
          <span className="w-2.5 h-2.5 rounded-full bg-[var(--theme-success)]" />
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {t("documents.htmlDocument")}
          </span>
          <span className="text-[11px] text-neutral-400 dark:text-neutral-500 tabular-nums">
            {content.length.toLocaleString()} chars
          </span>
        </div>

        <div className="flex items-center gap-0.5 bg-neutral-100 dark:bg-neutral-800 rounded-md p-0.5">
          <Button type="text"
            onClick={() => setShowSource(false)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-all ${
              !showSource
                ? "bg-white dark:bg-neutral-700 text-neutral-700 dark:text-neutral-200 shadow-sm"
                : "text-neutral-500 dark:text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-300"
            }`}
          >
            <Eye size={13} />
            <span>{t("documents.preview")}</span>
          </Button>
          <Button type="text"
            onClick={() => setShowSource(true)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-all ${
              showSource
                ? "bg-white dark:bg-neutral-700 text-neutral-700 dark:text-neutral-200 shadow-sm"
                : "text-neutral-500 dark:text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-300"
            }`}
          >
            <Code size={13} />
            <span>{t("documents.source")}</span>
          </Button>
        </div>
      </div>

      {/* HTML content */}
      <div className="flex-1 overflow-hidden">
        {showSource ? (
          <DeferredCodeMirrorViewer
            value={content}
            language="html"
            lineNumbers={true}
            fontSize="0.8125rem"
            className="w-full h-full"
          />
        ) : (
          <iframe
            srcDoc={previewContent}
            title={t("documents.htmlDocument")}
            className="w-full h-full border-0"
            sandbox=""
            referrerPolicy="no-referrer"
          />
        )}
      </div>
    </div>
  );
});

export default HtmlPreview;
