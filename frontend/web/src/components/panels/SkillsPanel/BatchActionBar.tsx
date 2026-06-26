import { useTranslation } from "react-i18next";
import { Power, Zap, Trash2, X } from "lucide-react";
import { LoadingSpinner } from "../../common/LoadingSpinner";

interface BatchActionBarProps {
  selectedCount: number;
  batchLoading: boolean;
  canWrite: boolean;
  canDelete: boolean;
  onBatchToggle: (enabled: boolean) => void;
  onBatchDelete: () => void;
  onClearSelection: () => void;
}

export function BatchActionBar({
  selectedCount,
  batchLoading,
  canWrite,
  canDelete,
  onBatchToggle,
  onBatchDelete,
  onClearSelection,
}: BatchActionBarProps) {
  const { t } = useTranslation();

  return (
    <div className="fixed bottom-4 left-4 right-4 z-40 flex justify-center sm:bottom-6 sm:left-1/2 sm:right-auto sm:-translate-x-1/2">
      <div className="flex items-center gap-1 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-workbench-panel)] px-2 py-1.5 shadow-[0_12px_28px_rgba(15,23,42,0.12)]">
        <span className="mr-1.5 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-[var(--theme-primary)] px-1 text-[10px] font-bold leading-none text-[var(--theme-primary-foreground)]">
          {selectedCount}
        </span>
        <span className="mr-1 text-xs text-[var(--theme-text-secondary)] hidden sm:inline">
          {t("skills.batchSelected")}
        </span>
        {(canWrite || canDelete) && (
          <div className="w-px h-4 bg-[var(--theme-border)]" />
        )}
        {canWrite && (
          <>
            <button
              onClick={() => onBatchToggle(false)}
              disabled={batchLoading}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)] disabled:opacity-40 disabled:pointer-events-none"
            >
              <Power size={13} />
              <span className="hidden sm:inline">
                {t("skills.card.disable")}
              </span>
            </button>
            <button
              onClick={() => onBatchToggle(true)}
              disabled={batchLoading}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text)] disabled:opacity-40 disabled:pointer-events-none"
            >
              <Zap size={13} />
              <span className="hidden sm:inline">
                {t("skills.card.enable")}
              </span>
            </button>
          </>
        )}
        {canDelete && (
          <button
            onClick={onBatchDelete}
            disabled={batchLoading}
            className="inline-flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-[var(--theme-danger)] transition-colors hover:bg-[var(--theme-danger-soft)] disabled:opacity-40 disabled:pointer-events-none"
          >
            {batchLoading ? <LoadingSpinner size="xs" /> : <Trash2 size={13} />}
            <span className="hidden sm:inline">{t("common.delete")}</span>
          </button>
        )}
        <button
          onClick={onClearSelection}
          className="inline-flex items-center justify-center w-6 h-6 rounded-lg text-[var(--theme-text-tertiary)] transition-colors hover:bg-[var(--theme-hover)] hover:text-[var(--theme-text-secondary)]"
        >
          <X size={13} />
        </button>
      </div>
    </div>
  );
}
