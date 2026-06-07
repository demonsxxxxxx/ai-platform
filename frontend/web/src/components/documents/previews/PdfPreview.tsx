import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Document, Page, pdfjs } from "react-pdf";
import { Maximize2, Minus, Plus } from "lucide-react";
import pdfWorkerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { LoadingSpinner } from "../../common/LoadingSpinner";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerUrl;

interface PdfPreviewProps {
  url: string;
}

const MIN_SCALE = 0.5;
const MAX_SCALE = 3;
const SCALE_STEP = 0.2;
const MIN_PAGE_WIDTH = 260;
const MAX_FIT_WIDTH = 980;
const DOUBLE_TAP_DELAY_MS = 280;
const DOUBLE_TAP_SCALE = 1.8;
const TAP_MOVE_TOLERANCE = 8;

interface TouchPoint {
  x: number;
  y: number;
}

interface PanStart extends TouchPoint {
  scrollLeft: number;
  scrollTop: number;
}

interface PinchStart {
  distance: number;
  scale: number;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

function getPinchDistance(touches: React.TouchList): number {
  return Math.hypot(
    touches[0].clientX - touches[1].clientX,
    touches[0].clientY - touches[1].clientY,
  );
}

const PdfPreview = memo(function PdfPreview({ url }: PdfPreviewProps) {
  const { t } = useTranslation();
  const containerRef = useRef<HTMLDivElement>(null);
  const lastTapAtRef = useRef(0);
  const touchMovedRef = useRef(false);
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1);
  const [containerWidth, setContainerWidth] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [panStart, setPanStart] = useState<PanStart | null>(null);
  const [pinchStart, setPinchStart] = useState<PinchStart | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const updateWidth = () => setContainerWidth(container.clientWidth);
    updateWidth();

    const resizeObserver = new ResizeObserver(updateWidth);
    resizeObserver.observe(container);
    return () => resizeObserver.disconnect();
  }, []);

  useEffect(() => {
    setNumPages(0);
    setScale(1);
    setLoading(true);
    setLoadFailed(false);
    setPanStart(null);
    setPinchStart(null);
  }, [url]);

  const fitPageWidth = useMemo(() => {
    if (!containerWidth) return 720;
    return clamp(containerWidth - 32, MIN_PAGE_WIDTH, MAX_FIT_WIDTH);
  }, [containerWidth]);

  const pageWidth = Math.round(fitPageWidth * scale);
  const pageCountLabel = numPages
    ? t("documents.pdfPageCount", "{{count}} 页", { count: numPages })
    : t("documents.pdfPreviewTitle", "PDF 预览");

  const zoomOut = useCallback(() => {
    setScale((current) =>
      Number(clamp(current - SCALE_STEP, MIN_SCALE, MAX_SCALE).toFixed(2)),
    );
  }, []);

  const zoomIn = useCallback(() => {
    setScale((current) =>
      Number(clamp(current + SCALE_STEP, MIN_SCALE, MAX_SCALE).toFixed(2)),
    );
  }, []);

  const fitWidth = useCallback(() => {
    setScale(1);
  }, []);

  const handleDoubleTapZoom = useCallback(() => {
    setScale((current) =>
      current > 1 ? 1 : Math.min(DOUBLE_TAP_SCALE, MAX_SCALE),
    );
  }, []);

  const handleTouchStart = useCallback(
    (event: React.TouchEvent<HTMLDivElement>) => {
      if (event.touches.length === 1) {
        const touch = event.touches[0];
        const container = containerRef.current;
        if (!container) return;

        setPanStart({
          x: touch.clientX,
          y: touch.clientY,
          scrollLeft: container.scrollLeft,
          scrollTop: container.scrollTop,
        });
        touchMovedRef.current = false;
        setPinchStart(null);
        return;
      }

      if (event.touches.length === 2) {
        event.preventDefault();
        setPanStart(null);
        setPinchStart({
          distance: getPinchDistance(event.touches),
          scale,
        });
      }
    },
    [scale],
  );

  const handleTouchMove = useCallback(
    (event: React.TouchEvent<HTMLDivElement>) => {
      const container = containerRef.current;
      if (!container) return;

      if (event.touches.length === 2 && pinchStart) {
        event.preventDefault();
        touchMovedRef.current = true;
        const distance = getPinchDistance(event.touches);
        const nextScale = clamp(
          pinchStart.scale * (distance / pinchStart.distance),
          MIN_SCALE,
          MAX_SCALE,
        );
        setScale(Number(nextScale.toFixed(2)));
        return;
      }

      if (event.touches.length === 1 && panStart) {
        event.preventDefault();
        const touch = event.touches[0];
        if (
          Math.abs(touch.clientX - panStart.x) > TAP_MOVE_TOLERANCE ||
          Math.abs(touch.clientY - panStart.y) > TAP_MOVE_TOLERANCE
        ) {
          touchMovedRef.current = true;
        }
        container.scrollLeft =
          panStart.scrollLeft - (touch.clientX - panStart.x);
        container.scrollTop = panStart.scrollTop - (touch.clientY - panStart.y);
      }
    },
    [panStart, pinchStart],
  );

  const handleTouchEnd = useCallback(
    (event: React.TouchEvent<HTMLDivElement>) => {
      if (event.touches.length === 0) {
        setPanStart(null);
        setPinchStart(null);

        if (event.changedTouches.length === 1 && !touchMovedRef.current) {
          const now = Date.now();
          if (now - lastTapAtRef.current < DOUBLE_TAP_DELAY_MS) {
            handleDoubleTapZoom();
            lastTapAtRef.current = 0;
            return;
          }
          lastTapAtRef.current = now;
        }
      }
    },
    [handleDoubleTapZoom],
  );

  if (loadFailed) {
    return (
      <div className="flex h-full min-h-[400px] w-full flex-col items-center justify-center gap-4 bg-stone-100 px-6 text-center dark:bg-stone-950">
        <div>
          <p className="text-sm font-medium text-stone-700 dark:text-stone-200">
            {t("documents.pdfPreviewUnavailable", "PDF 预览不可用")}
          </p>
          <p className="mt-1 max-w-sm text-xs text-stone-500 dark:text-stone-400">
            {t(
              "documents.pdfPreviewUnavailableHint",
              "当前浏览器无法在页面内打开这个 PDF，可以在新窗口中查看。",
            )}
          </p>
        </div>
        <a
          href={url}
          target="_blank"
          rel="noreferrer"
          className="rounded-lg bg-stone-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-stone-700 dark:bg-stone-100 dark:text-stone-900 dark:hover:bg-stone-300"
        >
          {t("documents.openInNewTab", "在新窗口打开")}
        </a>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 w-full flex-col overflow-hidden bg-stone-200 dark:bg-stone-950">
      <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-stone-300 bg-stone-100 px-2.5 py-2 dark:border-stone-800 dark:bg-stone-900 sm:px-3">
        <span className="min-w-0 truncate text-xs font-medium text-stone-500 dark:text-stone-400">
          {pageCountLabel}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={zoomOut}
            disabled={scale <= MIN_SCALE}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-stone-500 transition-colors hover:bg-stone-200 disabled:cursor-not-allowed disabled:opacity-30 dark:text-stone-400 dark:hover:bg-stone-800"
            title={t("documents.zoomOut")}
          >
            <Minus size={16} />
          </button>
          <button
            type="button"
            onClick={fitWidth}
            className="inline-flex h-8 min-w-14 items-center justify-center rounded-md px-2 text-xs font-medium tabular-nums text-stone-500 transition-colors hover:bg-stone-200 dark:text-stone-400 dark:hover:bg-stone-800"
            title={t("documents.fitWidth", "适合宽度")}
          >
            {Math.round(scale * 100)}%
          </button>
          <button
            type="button"
            onClick={zoomIn}
            disabled={scale >= MAX_SCALE}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-stone-500 transition-colors hover:bg-stone-200 disabled:cursor-not-allowed disabled:opacity-30 dark:text-stone-400 dark:hover:bg-stone-800"
            title={t("documents.zoomIn")}
          >
            <Plus size={16} />
          </button>
          <button
            type="button"
            onClick={fitWidth}
            className="inline-flex h-8 w-8 items-center justify-center rounded-md text-stone-500 transition-colors hover:bg-stone-200 dark:text-stone-400 dark:hover:bg-stone-800"
            title={t("documents.fitWidth", "适合宽度")}
          >
            <Maximize2 size={16} />
          </button>
        </div>
      </div>

      <div
        ref={containerRef}
        className="relative min-h-0 flex-1 overflow-auto px-3 py-4 sm:px-5 sm:py-5"
        onDoubleClick={handleDoubleTapZoom}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{ touchAction: "none" }}
      >
        {loading && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-stone-100/80 backdrop-blur-sm dark:bg-stone-950/80">
            <LoadingSpinner
              className="text-stone-400 dark:text-stone-500"
              size="lg"
            />
          </div>
        )}
        <Document
          file={url}
          onLoadSuccess={({ numPages }) => {
            setNumPages(numPages);
            setLoading(false);
          }}
          onLoadError={() => {
            setLoading(false);
            setLoadFailed(true);
          }}
          loading={null}
          error={null}
          className="flex min-w-max flex-col items-center gap-4 sm:gap-5"
        >
          {Array.from({ length: numPages }, (_, pageNumber) => (
            <Page
              key={`page_${pageNumber + 1}`}
              pageNumber={pageNumber + 1}
              width={pageWidth}
              renderTextLayer={false}
              renderAnnotationLayer={false}
              loading={
                <div
                  className="flex items-center justify-center bg-white shadow-xl ring-1 ring-black/5 dark:bg-stone-900 dark:ring-white/10"
                  style={{
                    width: pageWidth,
                    minHeight: Math.round(pageWidth * 1.35),
                  }}
                >
                  <LoadingSpinner
                    className="text-stone-300 dark:text-stone-600"
                    size="sm"
                  />
                </div>
              }
              className="overflow-hidden bg-white shadow-xl ring-1 ring-black/5 dark:bg-stone-900 dark:ring-white/10"
            />
          ))}
        </Document>
      </div>
    </div>
  );
});

export default PdfPreview;
