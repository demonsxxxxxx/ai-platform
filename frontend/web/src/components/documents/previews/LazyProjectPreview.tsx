import { Suspense, lazy } from "react";
import type { ComponentProps } from "react";
import { useTranslation } from "react-i18next";
import { Loading } from "../../common/LoadingSpinner";
import type ProjectPreview from "./ProjectPreview";

const ProjectPreviewContent = lazy(() => import("./ProjectPreview"));

type LazyProjectPreviewProps = ComponentProps<typeof ProjectPreview>;

function ProjectPreviewFallback() {
  const { t } = useTranslation();
  return (
    <div className="flex h-full min-h-[220px] items-center justify-center bg-stone-50 dark:bg-stone-900/95">
      <Loading text={t("project.loadingPreview")} />
    </div>
  );
}

export function LazyProjectPreview(props: LazyProjectPreviewProps) {
  return (
    <Suspense fallback={<ProjectPreviewFallback />}>
      <ProjectPreviewContent {...props} />
    </Suspense>
  );
}
