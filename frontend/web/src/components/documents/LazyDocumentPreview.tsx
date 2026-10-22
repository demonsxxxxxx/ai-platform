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
    <div className="fixed inset-0 z-[90] flex items-center justify-center bg-[color-mix(in_srgb,var(--theme-overlay-solid)_50%,transparent)]">
      <div className="rounded-2xl bg-[color-mix(in_srgb,var(--theme-on-overlay)_90%,transparent)] px-4 py-3 shadow-xl dark:bg-neutral-900/90">
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
