import { useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { X, Download } from "lucide-react";

interface VideoViewerProps {
  src: string;
  isOpen: boolean;
  onClose: () => void;
  title?: string;
}

export function VideoViewer({ src, isOpen, onClose, title }: VideoViewerProps) {
  const { t } = useTranslation();
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (isOpen && videoRef.current) {
      videoRef.current.currentTime = 0;
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen && videoRef.current) {
      videoRef.current.pause();
    }
  }, [isOpen]);

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
      className="fixed inset-0 z-[300] flex flex-col bg-black"
      onClick={handleBackgroundClick}
    >
      <div className="flex items-center justify-between px-3 sm:px-6 py-3 bg-black/80 shrink-0">
        <button
          type="button"
          onClick={onClose}
          className="flex items-center justify-center w-10 h-10 rounded-lg hover:bg-white/10 transition-colors cursor-pointer"
          aria-label={t("common.close")}
        >
          <X size={20} className="text-white/70" />
        </button>
        {title && (
          <span className="text-sm text-white/70 truncate max-w-[60vw] hidden sm:block">
            {title}
          </span>
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

      <div className="flex-1 overflow-hidden flex items-center justify-center">
        <video
          ref={videoRef}
          controls
          autoPlay={false}
          className="max-w-full max-h-full"
          src={src}
          onClick={(e) => e.stopPropagation()}
        >
          {t("documents.videoNotSupported")}
        </video>
      </div>
    </div>,
    document.body,
  );
}
