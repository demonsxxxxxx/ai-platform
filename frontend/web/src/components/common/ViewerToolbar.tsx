import { Button } from "@lobehub/ui/base-ui";
import { ZoomIn, ZoomOut, RotateCcw, RotateCw, Shrink } from "lucide-react";
import { useTranslation } from "react-i18next";

interface ViewerToolbarProps {
  scale: number;
  minScale?: number;
  maxScale?: number;
  scaleStep?: number;
  showRotation?: boolean;
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
  showRotation = true,
  onZoomIn,
  onZoomOut,
  onRotateLeft,
  onRotateRight,
  onReset,
}: ViewerToolbarProps) {
  const { t } = useTranslation();
  const scalePercentage = Math.round(scale * 100);

  const labelClass = "hidden sm:inline text-xs";

  return (
    <div className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-0.5 rounded-lg border border-[var(--theme-border)] bg-[var(--theme-bg-card)] px-1.5 py-1.5 text-[var(--theme-text)] shadow-[0_8px_18px_rgba(18,38,63,0.08)] [padding-bottom:max(env(safe-area-inset-bottom),6px)] dark:bg-neutral-900 sm:bottom-8 sm:gap-1 sm:px-2 sm:py-2">
      {showRotation && (
        <>
          <div className="flex items-center rounded-lg transition-colors">
            <Button type="text"
              htmlType="button"
              onClick={onRotateLeft}
              size="large"
              style={{ paddingInline: 8 }}
              icon={<RotateCcw size={18} />}
              aria-label={t("imageViewer.rotateLeft")}
            >
              <span className={labelClass}>{t("imageViewer.rotateLeft")}</span>
            </Button>

            <Button type="text"
              htmlType="button"
              onClick={onRotateRight}
              size="large"
              style={{ paddingInline: 8 }}
              icon={<RotateCw size={18} />}
              aria-label={t("imageViewer.rotateRight")}
            >
              <span className={labelClass}>{t("imageViewer.rotateRight")}</span>
            </Button>
          </div>

          <div className="mx-0.5 h-5 w-px bg-[var(--theme-border)] sm:mx-1 sm:h-6" />
        </>
      )}

      <div className="flex items-center rounded-lg transition-colors">
        <Button type="text"
          htmlType="button"
          onClick={onZoomOut}
          disabled={scale <= minScale}
          size="large"
          style={{ paddingInline: 8 }}
          icon={<ZoomOut size={18} />}
          aria-label={t("imageViewer.zoomOut")}
        >
          <span className={labelClass}>{t("imageViewer.zoomOut")}</span>
        </Button>

        <span className="min-w-[48px] text-center text-xs font-medium tabular-nums text-[var(--theme-text-secondary)] sm:min-w-[52px] sm:text-sm">
          {scalePercentage}%
        </span>

        <Button type="text"
          htmlType="button"
          onClick={onZoomIn}
          disabled={scale >= maxScale}
          size="large"
          style={{ paddingInline: 8 }}
          icon={<ZoomIn size={18} />}
          aria-label={t("imageViewer.zoomIn")}
        >
          <span className={labelClass}>{t("imageViewer.zoomIn")}</span>
        </Button>
      </div>

      <div className="mx-0.5 h-5 w-px bg-[var(--theme-border)] sm:mx-1 sm:h-6" />

      <div className="flex items-center rounded-lg transition-colors">
        <Button type="text"
          htmlType="button"
          onClick={onReset}
          size="large"
          style={{ paddingInline: 8 }}
          icon={<Shrink size={18} />}
          aria-label={t("imageViewer.reset")}
        >
          <span className={labelClass}>{t("imageViewer.reset")}</span>
        </Button>
      </div>
    </div>
  );
}
