import { Button } from "@lobehub/ui/base-ui";
import { useEffect, useCallback, useRef } from "react";
import { createPortal } from "react-dom";
import { useAppElement } from "@lobehub/ui/es/ThemeProvider/index";
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
  const appElement = useAppElement();
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
        <Button type="text"
          htmlType="button"
          onClick={onClose}
          size="large"
          icon={<X size={20} className="text-white/70" />}
          aria-label={t("common.close")}
        />
        {title && (
          <span className="text-sm text-white/70 truncate max-w-[60vw] hidden sm:block">
            {title}
          </span>
        )}
        <Button type="text"
          htmlType="button"
          onClick={() => {
            const a = document.createElement("a");
            a.href = src;
            a.download = "";
            a.click();
          }}
          size="large"
          className="text-white/70"
          icon={<Download size={18} className="text-white/70" />}
          aria-label={t("imageViewer.download")}
        >
          <span className="hidden sm:inline">{t("imageViewer.download")}</span>
        </Button>
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
    appElement ?? document.body,
  );
}
