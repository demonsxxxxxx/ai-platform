import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import { ViewerToolbar } from "../../common/ViewerToolbar";
import { LoadingSpinner } from "../../common/LoadingSpinner";

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 3;
const ZOOM_STEP = 0.2;

interface DocumentViewerFrameProps {
  naturalWidth: number;
  loading?: boolean;
  ariaLabel?: string;
  children: (displayScale: number) => ReactNode;
}

interface ScaledDocumentContentProps {
  naturalWidth: number;
  naturalHeight: number;
  displayScale: number;
  contentRef?: Ref<HTMLDivElement>;
  className?: string;
  children?: ReactNode;
}

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
  zoom: number;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function ScaledDocumentContent({
  naturalWidth,
  naturalHeight,
  displayScale,
  contentRef,
  className,
  children,
}: ScaledDocumentContentProps) {
  return (
    <div
      className="relative shrink-0"
      style={{
        width: naturalWidth * displayScale,
        height: naturalHeight * displayScale,
      }}
    >
      <div
        ref={contentRef}
        className={className}
        style={{
          width: naturalWidth,
          height: naturalHeight,
          transform: `scale(${displayScale})`,
          transformOrigin: "top left",
        }}
      >
        {children}
      </div>
    </div>
  );
}

export function DocumentViewerFrame({
  naturalWidth,
  loading = false,
  ariaLabel,
  children,
}: DocumentViewerFrameProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const lastTapAtRef = useRef(0);
  const touchMovedRef = useRef(false);
  const panStartRef = useRef<PanStart | null>(null);
  const pinchStartRef = useRef<PinchStart | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);
  const [zoom, setZoom] = useState(1);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;

    const updateWidth = () => setViewportWidth(viewport.clientWidth);
    updateWidth();

    const observer = new ResizeObserver(updateWidth);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  const fitScale = useMemo(() => {
    if (viewportWidth <= 0 || naturalWidth <= 0) return 1;
    return Math.min(1, Math.max(0.1, (viewportWidth - 40) / naturalWidth));
  }, [naturalWidth, viewportWidth]);
  const displayScale = fitScale * zoom;

  const zoomIn = useCallback(
    () =>
      setZoom((current) =>
        Number(clamp(current + ZOOM_STEP, MIN_ZOOM, MAX_ZOOM).toFixed(2)),
      ),
    [],
  );
  const zoomOut = useCallback(
    () =>
      setZoom((current) =>
        Number(clamp(current - ZOOM_STEP, MIN_ZOOM, MAX_ZOOM).toFixed(2)),
      ),
    [],
  );
  const resetZoom = useCallback(() => setZoom(1), []);
  const toggleDoubleTapZoom = useCallback(() => {
    setZoom((current) => (current > 1 ? 1 : Math.min(1.8, MAX_ZOOM)));
  }, []);
  const handleTouchStart = useCallback(
    (event: React.TouchEvent<HTMLDivElement>) => {
      const viewport = viewportRef.current;
      if (!viewport) return;

      if (event.touches.length === 1) {
        const touch = event.touches[0];
        panStartRef.current = {
          x: touch.clientX,
          y: touch.clientY,
          scrollLeft: viewport.scrollLeft,
          scrollTop: viewport.scrollTop,
        };
        pinchStartRef.current = null;
        touchMovedRef.current = false;
      } else if (event.touches.length === 2) {
        const [first, second] = [event.touches[0], event.touches[1]];
        pinchStartRef.current = {
          distance: Math.hypot(
            first.clientX - second.clientX,
            first.clientY - second.clientY,
          ),
          zoom,
        };
        panStartRef.current = null;
      }
    },
    [zoom],
  );
  const handleTouchMove = useCallback(
    (event: React.TouchEvent<HTMLDivElement>) => {
      const viewport = viewportRef.current;
      if (!viewport) return;

      if (event.touches.length === 2 && pinchStartRef.current) {
        event.preventDefault();
        touchMovedRef.current = true;
        const [first, second] = [event.touches[0], event.touches[1]];
        const distance = Math.hypot(
          first.clientX - second.clientX,
          first.clientY - second.clientY,
        );
        setZoom(
          Number(
            clamp(
              pinchStartRef.current.zoom *
                (distance / pinchStartRef.current.distance),
              MIN_ZOOM,
              MAX_ZOOM,
            ).toFixed(2),
          ),
        );
        return;
      }

      if (event.touches.length === 1 && panStartRef.current) {
        event.preventDefault();
        const touch = event.touches[0];
        if (
          Math.abs(touch.clientX - panStartRef.current.x) > 8 ||
          Math.abs(touch.clientY - panStartRef.current.y) > 8
        ) {
          touchMovedRef.current = true;
        }
        viewport.scrollLeft =
          panStartRef.current.scrollLeft -
          (touch.clientX - panStartRef.current.x);
        viewport.scrollTop =
          panStartRef.current.scrollTop -
          (touch.clientY - panStartRef.current.y);
      }
    },
    [],
  );
  const handleTouchEnd = useCallback(
    (event: React.TouchEvent<HTMLDivElement>) => {
      if (event.touches.length > 0) return;

      panStartRef.current = null;
      pinchStartRef.current = null;
      if (event.changedTouches.length !== 1 || touchMovedRef.current) return;

      const now = Date.now();
      if (now - lastTapAtRef.current < 280) {
        toggleDoubleTapZoom();
        lastTapAtRef.current = 0;
      } else {
        lastTapAtRef.current = now;
      }
    },
    [toggleDoubleTapZoom],
  );

  return (
    <div className="relative h-full min-h-[400px] w-full overflow-hidden bg-stone-200 dark:bg-stone-950">
      <div
        ref={viewportRef}
        aria-label={ariaLabel}
        className="h-full min-h-0 w-full overflow-auto"
        onDoubleClick={toggleDoubleTapZoom}
        onTouchStart={handleTouchStart}
        onTouchMove={handleTouchMove}
        onTouchEnd={handleTouchEnd}
        style={{ touchAction: "none" }}
      >
        <div className="box-border flex min-h-full min-w-full w-max items-start justify-center px-3 py-4 pb-24 sm:px-5 sm:py-5 sm:pb-28">
          {children(displayScale)}
        </div>
      </div>

      {loading ? (
        <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center bg-stone-100/80 dark:bg-stone-950/80">
          <LoadingSpinner
            className="text-stone-400 dark:text-stone-500"
            size="lg"
          />
        </div>
      ) : (
        <ViewerToolbar
          scale={zoom}
          minScale={MIN_ZOOM}
          maxScale={MAX_ZOOM}
          showRotation={false}
          onZoomIn={zoomIn}
          onZoomOut={zoomOut}
          onRotateLeft={() => {}}
          onRotateRight={() => {}}
          onReset={resetZoom}
        />
      )}
    </div>
  );
}
