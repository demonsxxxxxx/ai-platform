import { Suspense, lazy } from "react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { Spin } from "@lobehub/ui/base-ui";
import type DocumentPreview from "./DocumentPreview";

const DocumentPreviewContent = lazy(() => import("./DocumentPreview"));

type LazyDocumentPreviewProps = ComponentProps<typeof DocumentPreview>;

function DocumentPreviewFallback() {
  const { t } = useTranslation();
  return (
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-black/50">
      <div className="rounded-2xl bg-white/90 px-4 py-3 shadow-xl dark:bg-stone-900/90">
        <div className="flex items-center gap-2" role="status">
          <Spin size={24} />
          <span>{t("documents.loadingPreview")}</span>
        </div>
      </div>
    </div>
  );
}

export function LazyDocumentPreview(props: LazyDocumentPreviewProps) {
  return (
    <Suspense fallback={<DocumentPreviewFallback />}>
      <DocumentPreviewContent {...props} />
    </Suspense>
  );
}
