import { useState, useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { X, Download, ChevronLeft, ChevronRight } from "lucide-react";
import { ViewerToolbar } from "./ViewerToolbar";

interface ImageViewerProps {
  src: string;
  alt?: string;
  isOpen: boolean;
  onClose: () => void;
  onPrevious?: () => void;
  onNext?: () => void;
  hasPrevious?: boolean;
  hasNext?: boolean;
  positionLabel?: string;
}

const MIN_SCALE = 0.1;
const MAX_SCALE = 20;
const SCALE_STEP = 0.25;

export function ImageViewer({
  src,
  alt = "",
  isOpen,
  onClose,
  onPrevious,
  onNext,
  hasPrevious = false,
  hasNext = false,
  positionLabel,
}: ImageViewerProps) {
  const { t } = useTranslation();
  const [scale, setScale] = useState(1);
  const [rotation, setRotation] = useState(0);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [isImageLoading, setIsImageLoading] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const containerRef = useRef<HTMLDivElement>(null);

  const [touchStart, setTouchStart] = useState<{ x: number; y: number } | null>(
    null,
  );
  const [initialPinchDistance, setInitialPinchDistance] = useState<
    number | null
  >(null);
  const [initialScale, setInitialScale] = useState(1);
  const canGoPrevious = hasPrevious && !!onPrevious;
  const canGoNext = hasNext && !!onNext;

  useEffect(() => {
    if (isOpen) {
      setScale(1);
      setRotation(0);
      setPosition({ x: 0, y: 0 });
      setIsImageLoading(true);
    }
  }, [isOpen, src]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      if (e.key === "ArrowLeft" && canGoPrevious) {
        e.preventDefault();
        onPrevious?.();
        return;
      }
      if (e.key === "ArrowRight" && canGoNext) {
        e.preventDefault();
        onNext?.();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [canGoNext, canGoPrevious, isOpen, onClose, onNext, onPrevious]);

  useEffect(() => {
    if (!isOpen) return;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -SCALE_STEP : SCALE_STEP;
    setScale((prev) => Math.min(MAX_SCALE, Math.max(MIN_SCALE, prev + delta)));
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (e.button !== 0) return;
      e.preventDefault();
      setIsDragging(true);
      setDragStart({ x: e.clientX - position.x, y: e.clientY - position.y });
    },
    [position],
  );

  const getPinchDistance = (touches: React.TouchList): number => {
    return Math.hypot(
      touches[0].clientX - touches[1].clientX,
      touches[0].clientY - touches[1].clientY,
    );
  };

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      if (e.touches.length === 1) {
        const touch = e.touches[0];
        setTouchStart({
          x: touch.clientX - position.x,
          y: touch.clientY - position.y,
        });
        setIsDragging(true);
      } else if (e.touches.length === 2) {
        setIsDragging(false);
        setTouchStart(null);
        const distance = getPinchDistance(e.touches);
        setInitialPinchDistance(distance);
        setInitialScale(scale);
      }
    },
    [position, scale],
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      e.preventDefault();
      if (e.touches.length === 1 && touchStart) {
        const touch = e.touches[0];
        setPosition({
          x: touch.clientX - touchStart.x,
          y: touch.clientY - touchStart.y,
        });
      } else if (e.touches.length === 2 && initialPinchDistance !== null) {
        const currentDistance = getPinchDistance(e.touches);
        const scaleFactor = currentDistance / initialPinchDistance;
        const newScale = Math.min(
          MAX_SCALE,
          Math.max(MIN_SCALE, initialScale * scaleFactor),
        );
        setScale(newScale);
      }
    },
    [touchStart, initialPinchDistance, initialScale],
  );

  const handleTouchEnd = useCallback(() => {
    setIsDragging(false);
    setTouchStart(null);
    setInitialPinchDistance(null);
  }, []);

  useEffect(() => {
    if (!isDragging) return;
    const handleMouseMove = (e: MouseEvent) => {
      setPosition({
        x: e.clientX - dragStart.x,
        y: e.clientY - dragStart.y,
      });
    };
    const handleMouseUp = () => {
      setIsDragging(false);
    };
    document.addEventListener("mousemove", handleMouseMove);
    document.addEventListener("mouseup", handleMouseUp);
    return () => {
      document.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, dragStart]);

  const zoomIn = useCallback(() => {
    setScale((prev) => Math.min(MAX_SCALE, prev + SCALE_STEP));
  }, []);

  const zoomOut = useCallback(() => {
    setScale((prev) => Math.max(MIN_SCALE, prev - SCALE_STEP));
  }, []);

  const rotateLeft = useCallback(() => {
    setRotation((prev) => prev - 90);
  }, []);

  const rotateRight = useCallback(() => {
    setRotation((prev) => prev + 90);
  }, []);

  const reset = useCallback(() => {
    setScale(1);
    setRotation(0);
    setPosition({ x: 0, y: 0 });
  }, []);

  const handleBackgroundClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!isOpen) return null;

  return createPortal(
    <div
      data-yields-sidebar
      className="fixed inset-0 z-[300] flex flex-col bg-black/90"
      onClick={handleBackgroundClick}
    >
      {/* Top bar */}
      <div className="flex items-center justify-between px-3 sm:px-6 py-3 bg-black">
        <button
          type="button"
          onClick={onClose}
          className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-white/10 transition-colors cursor-pointer"
          aria-label={t("common.close")}
        >
          <X size={20} className="text-white/70" />
        </button>

        {positionLabel && (
          <div className="pointer-events-none absolute left-1/2 -translate-x-1/2 rounded-md bg-white/10 px-2.5 py-1 text-xs font-medium tabular-nums text-white/70">
            {positionLabel}
          </div>
        )}

        <button
          type="button"
          onClick={() => {
            const a = document.createElement("a");
            a.href = src;
            a.download = "";
            a.click();
          }}
          className="flex items-center gap-1.5 rounded-lg px-3 h-10 text-sm font-medium transition-colors cursor-pointer hover:bg-white/10 text-white/70"
          aria-label={t("imageViewer.download")}
        >
          <Download size={18} className="text-white/70" />
          <span className="hidden sm:inline">{t("imageViewer.download")}</span>
        </button>
      </div>

      {/* Main area */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden relative"
        onWheel={handleWheel}
      >
        <div
          className="absolute inset-0 flex items-center justify-center"
          style={{
            cursor: scale > 1 ? (isDragging ? "grabbing" : "grab") : "default",
          }}
        >
          <img
            src={src}
            alt={alt}
            className="max-w-full max-h-full object-contain select-none"
            style={{
              transform: `translate(${position.x}px, ${position.y}px) scale(${scale}) rotate(${rotation}deg)`,
              opacity: isImageLoading ? 0.45 : 1,
              transition: isDragging
                ? "none"
                : "transform 0.1s ease-out, opacity 0.15s ease-out",
              touchAction: "none",
            }}
            onLoad={() => setIsImageLoading(false)}
            onError={() => setIsImageLoading(false)}
            onMouseDown={handleMouseDown}
            onTouchStart={handleTouchStart}
            onTouchMove={handleTouchMove}
            onTouchEnd={handleTouchEnd}
            draggable={false}
          />
        </div>

        {isImageLoading && (
          <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-black/20">
            <div className="flex items-center gap-2 rounded-full bg-black/65 px-3 py-2 text-xs font-medium text-white/80 shadow-lg backdrop-blur-sm">
              <span className="h-4 w-4 rounded-full border-2 border-white/25 border-t-white/85 animate-spin" />
              <span>{t("imageViewer.loading", "Loading image")}</span>
            </div>
          </div>
        )}

        {(onPrevious || onNext) && (
          <>
            <button
              type="button"
              onClick={onPrevious}
              disabled={!canGoPrevious}
              className="absolute left-2 sm:left-5 top-1/2 z-10 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/45 text-white/75 transition-colors hover:bg-black/65 hover:text-white disabled:cursor-not-allowed disabled:opacity-25 disabled:hover:bg-black/45 disabled:hover:text-white/75"
              aria-label={t("imageViewer.previous", "Previous image")}
              title={t("imageViewer.previous", "Previous image")}
            >
              <ChevronLeft size={28} />
            </button>
            <button
              type="button"
              onClick={onNext}
              disabled={!canGoNext}
              className="absolute right-2 sm:right-5 top-1/2 z-10 flex h-11 w-11 -translate-y-1/2 items-center justify-center rounded-full bg-black/45 text-white/75 transition-colors hover:bg-black/65 hover:text-white disabled:cursor-not-allowed disabled:opacity-25 disabled:hover:bg-black/45 disabled:hover:text-white/75"
              aria-label={t("imageViewer.next", "Next image")}
              title={t("imageViewer.next", "Next image")}
            >
              <ChevronRight size={28} />
            </button>
          </>
        )}

        <ViewerToolbar
          scale={scale}
          minScale={MIN_SCALE}
          maxScale={MAX_SCALE}
          onZoomIn={zoomIn}
          onZoomOut={zoomOut}
          onRotateLeft={rotateLeft}
          onRotateRight={rotateRight}
          onReset={reset}
        />
      </div>
    </div>,
    document.body,
  );
}
