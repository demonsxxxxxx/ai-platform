import { useTranslation } from "react-i18next";
import { FolderSearch, Search } from "lucide-react";
import { FilesContentSkeleton } from "../../skeletons";

interface EmptyStateProps {
  isLoading: boolean;
  hasFiles: boolean;
  hasActiveFilters: boolean;
}

export function EmptyState({
  isLoading,
  hasFiles,
  hasActiveFilters,
}: EmptyStateProps) {
  const { t } = useTranslation();

  /* Loading skeleton */
  if (isLoading) {
    return <FilesContentSkeleton />;
  }

  /* Empty states */
  if (!hasFiles) {
    return (
      <div className="enterprise-empty-state mx-5 my-6 min-h-72 rounded-lg border border-dashed border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-4">
        <div className="relative">
          <div className="enterprise-empty-state-icon">
            <FolderSearch
              size={32}
              strokeWidth={1.5}
              className="text-[var(--theme-text-secondary)]"
            />
          </div>
          <div className="absolute -bottom-1.5 -right-1.5 flex h-7 w-7 items-center justify-center rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] shadow-sm ring-4 ring-[var(--theme-workbench-canvas)]">
            <Search
              size={12}
              className="text-[var(--theme-text-secondary)]"
            />
          </div>
        </div>

        <div className="mt-5 space-y-1.5 text-center">
          <p className="text-sm font-medium text-[var(--theme-text-secondary)]">
            {hasActiveFilters
              ? t("fileLibrary.noResults")
              : t("fileLibrary.empty")}
          </p>
          {hasActiveFilters && (
            <p className="text-xs text-[var(--theme-text-secondary)]">
              {t("fileLibrary.tryDifferent")}
            </p>
          )}
        </div>
      </div>
    );
  }

  return null;
}
