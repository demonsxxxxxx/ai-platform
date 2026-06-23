import { ZoomIn, ZoomOut, RotateCcw, RotateCw, Shrink } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ViewerToolbarProps {
  scale: number;
  minScale?: number;
  maxScale?: number;
  scaleStep?: number;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onRotateLeft: () => void;
  onRotateRight: () => void;
  onReset: () => void;
}

export function ViewerToolbar({
  scale,
  minScale = 0.1,
  maxScale = 20,
  onZoomIn,
  onZoomOut,
  onRotateLeft,
  onRotateRight,
  onReset,
}: ViewerToolbarProps) {
  const { t } = useTranslation();
  const scalePercentage = Math.round(scale * 100);

  const buttonClass =
    "flex h-10 items-center justify-center gap-1.5 rounded-lg px-2.5 text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-sidebar)] hover:text-[var(--theme-text)] cursor-pointer";
  const iconClass = "text-[var(--theme-text-secondary)]";
  const labelClass = "hidden sm:inline text-xs";

  return (
    <div className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-0.5 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-1.5 py-1.5 text-[var(--theme-text)] shadow-[0_8px_18px_rgba(18,38,63,0.08)] [padding-bottom:max(env(safe-area-inset-bottom),6px)] dark:bg-stone-900 sm:bottom-8 sm:gap-1 sm:px-2 sm:py-2">
      <div className="flex items-center rounded-lg transition-colors">
        <button
          type="button"
          onClick={onRotateLeft}
          className={buttonClass}
          aria-label={t("imageViewer.rotateLeft")}
        >
          <RotateCcw size={18} className={iconClass} />
          <span className={labelClass}>{t("imageViewer.rotateLeft")}</span>
        </button>

        <button
          type="button"
          onClick={onRotateRight}
          className={buttonClass}
          aria-label={t("imageViewer.rotateRight")}
        >
          <RotateCw size={18} className={iconClass} />
          <span className={labelClass}>{t("imageViewer.rotateRight")}</span>
        </button>
      </div>

      <div className="mx-0.5 h-5 w-px bg-[var(--theme-border)] sm:mx-1 sm:h-6" />

      <div className="flex items-center rounded-lg transition-colors">
        <button
          type="button"
          onClick={onZoomOut}
          disabled={scale <= minScale}
          className={`${buttonClass} disabled:cursor-not-allowed disabled:opacity-50`}
          aria-label={t("imageViewer.zoomOut")}
        >
          <ZoomOut size={18} className={iconClass} />
          <span className={labelClass}>{t("imageViewer.zoomOut")}</span>
        </button>

        <span className="min-w-[48px] text-center text-xs font-medium tabular-nums text-[var(--theme-text-secondary)] sm:min-w-[52px] sm:text-sm">
          {scalePercentage}%
        </span>

        <button
          type="button"
          onClick={onZoomIn}
          disabled={scale >= maxScale}
          className={`${buttonClass} disabled:cursor-not-allowed disabled:opacity-50`}
          aria-label={t("imageViewer.zoomIn")}
        >
          <ZoomIn size={18} className={iconClass} />
          <span className={labelClass}>{t("imageViewer.zoomIn")}</span>
        </button>
      </div>

      <div className="mx-0.5 h-5 w-px bg-[var(--theme-border)] sm:mx-1 sm:h-6" />

      <div className="flex items-center rounded-lg transition-colors">
        <button
          type="button"
          onClick={onReset}
          className={buttonClass}
          aria-label={t("imageViewer.reset")}
        >
          <Shrink size={18} className={iconClass} />
          <span className={labelClass}>{t("imageViewer.reset")}</span>
        </button>
      </div>
    </div>
  );
}
