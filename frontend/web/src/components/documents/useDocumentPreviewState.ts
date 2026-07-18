import { useState, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { uploadApi } from "../../services/api";
import {
  getSidebarHistoryLength,
  goBackSidebar,
  subscribeSidebarHistory,
} from "../chat/ChatMessage/items/sidebarHistoryStore";
import {
  fetchDocumentArrayBuffer,
  fetchDocumentText,
  fetchXlsxPreviewJson,
  isUnsafeExternalHttpDocumentUrl,
  isUnsafeUnauthenticatedDocumentUrl,
} from "./documentFetchCache";
import {
  isAllowedAuthenticatedArtifactFileUrl,
  isSensitiveInternalPath,
} from "./documentUrlSafety";
import {
  downloadPreviewUrl,
  resolveDocumentPreviewUrl,
  resolvePptPreviewBuffer,
} from "./documentPreviewSources";
import {
  getFileExtension,
  isBinaryFile,
  isImageFile,
  isPdfFile,
  isWordPreviewFile,
  isLegacyDocFile,
  isCsvFile,
  isServerXlsxPreviewFile,
  isPptFile,
  isCadFile,
  isDxfFile,
  isDwgFile,
  isHtmlFile,
  isCodeFile,
  isMarkdownFile,
  isExcalidrawFile,
  isVideoFile,
  isAudioFile,
  getFileTypeInfo,
  detectLanguage,
} from "./utils";
import { copyToClipboard } from "../../utils/clipboard";

export interface DocumentPreviewProps {
  path: string;
  content?: string;
  s3Key?: string;
  signedUrl?: string;
  previewUrl?: string;
  downloadUrl?: string;
  fileSize?: number;
  imageUrl?: string;
  mimeType?: string;
  initialLine?: number;
  onClose: () => void;
  onUserInteraction?: () => void;
  registryKey?: string;
  onBack?: () => void;
  mobileFillViewport?: boolean;
}

const XLSX_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

type PreviewData = {
  content: string;
  path: string;
  identity: string;
};

export function assertSafeDocumentPreviewUrl(
  url: string,
  options: { currentOrigin?: string } = {},
): void {
  if (isSensitiveInternalPath(url)) {
    throw new Error("Unsafe internal preview URL");
  }
  if (isUnsafeExternalHttpDocumentUrl(url, options)) {
    throw new Error("Unsafe external http preview URL");
  }
  if (isUnsafeUnauthenticatedDocumentUrl(url, options)) {
    throw new Error("Unsafe unauthenticated preview URL");
  }
}

function isExactXlsxPreviewUrl(value: string | undefined): value is string {
  if (!value || !isAllowedAuthenticatedArtifactFileUrl(value)) {
    return false;
  }
  try {
    const base =
      typeof window === "undefined" ? "http://localhost" : window.location.origin;
    const segments = new URL(value, base).pathname.split("/").filter(Boolean);
    return (
      segments.length === 5 &&
      segments[0] === "api" &&
      segments[1] === "ai" &&
      (segments[2] === "artifacts" || segments[2] === "files") &&
      segments[4] === "preview"
    );
  } catch {
    return false;
  }
}

export function useDocumentPreviewState(props: DocumentPreviewProps) {
  const {
    path,
    content,
    s3Key,
    signedUrl,
    previewUrl,
    downloadUrl,
    fileSize,
    imageUrl: externalImageUrl,
    mimeType,
    onBack,
  } = props;

  const { t } = useTranslation();

  // Sidebar history
  const [historyAvailable, setHistoryAvailable] = useState(
    () => getSidebarHistoryLength() > 0,
  );
  useEffect(() => {
    return subscribeSidebarHistory(() => {
      setHistoryAvailable(getSidebarHistoryLength() > 0);
    });
  }, []);
  const effectiveOnBack =
    onBack ?? (historyAvailable ? goBackSidebar : undefined);

  // Data state
  const [data, setData] = useState<PreviewData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pptUrl, setPptUrl] = useState<string | null>(null);
  const [pptxBuffer, setPptxBuffer] = useState<ArrayBuffer | null>(null);
  const [cadUrl, setCadUrl] = useState<string | null>(null);
  const [cadKind, setCadKind] = useState<"dxf" | "dwg" | null>(null);
  const [htmlUrl, setHtmlUrl] = useState<string | null>(null);
  const [htmlContent, setHtmlContent] = useState<string>("");
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [docUrl, setDocUrl] = useState<string | null>(null);
  const [arrayBuffer, setArrayBuffer] = useState<ArrayBuffer | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showImageViewer, setShowImageViewer] = useState(false);
  const [excalidrawData, setExcalidrawData] = useState<string>("");
  const [viewSource, setViewSource] = useState(false);
  const [viewMode, setViewMode] = useState<"center" | "sidebar">("sidebar");
  const [resolvedUrl, setResolvedUrl] = useState<string | null>(null);
  const [isMobile, setIsMobile] = useState(() => window.innerWidth < 640);
  const toolbarRef = useRef<HTMLDivElement>(null);
  const [toolbarCompact, setToolbarCompact] = useState(false);
  const previewIdentity = useMemo(
    () =>
      [
        path,
        content ?? "",
        s3Key ?? "",
        previewUrl ?? "",
        signedUrl ?? "",
        externalImageUrl ?? "",
        mimeType ?? "",
      ].join("\u0000"),
    [content, externalImageUrl, mimeType, path, previewUrl, s3Key, signedUrl],
  );

  // Mobile detection
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 639px)");
    setIsMobile(mq.matches);
    const handler = (e: MediaQueryListEvent) => setIsMobile(e.matches);
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, []);

  // Toolbar resize observer
  useEffect(() => {
    const el = toolbarRef.current;
    if (!el) return;
    const ro = new ResizeObserver((entries) => {
      setToolbarCompact(entries[0].contentRect.width < 420);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // File type detection
  const fileName = path.split("/").pop() || path;
  const ext = getFileExtension(fileName);
  const binaryFile = isBinaryFile(ext);
  const imageFile = isImageFile(ext);
  const pdfFile = isPdfFile(ext);
  const wordPreviewFile = isWordPreviewFile(ext);
  const legacyDocFile = isLegacyDocFile(ext);
  const csvFile = isCsvFile(ext);
  const pptFile = isPptFile(ext);
  const cadFile = isCadFile(ext);
  const dxfFile = isDxfFile(ext);
  const dwgFile = isDwgFile(ext);
  const htmlFile = isHtmlFile(ext);
  const codeFile = isCodeFile(ext);
  const markdownFile = isMarkdownFile(fileName);
  const excalidrawFile = isExcalidrawFile(ext);
  const videoFile = isVideoFile(ext);
  const audioFile = isAudioFile(ext);

  // MIME-based fallback
  const mime = mimeType?.split(";", 1)[0]?.trim().toLowerCase();
  const xlsxPreviewUrl = isExactXlsxPreviewUrl(previewUrl)
    ? previewUrl
    : isExactXlsxPreviewUrl(signedUrl)
      ? signedUrl
      : undefined;
  const xlsxPreviewFile =
    isServerXlsxPreviewFile(ext) &&
    mime === XLSX_CONTENT_TYPE &&
    !!xlsxPreviewUrl;
  const resolvedImageFile = imageFile || !!mime?.startsWith("image/");
  const resolvedVideoFile = videoFile || !!mime?.startsWith("video/");
  const resolvedAudioFile = audioFile || !!mime?.startsWith("audio/");
  const resolvedPdfFile = pdfFile || mime === "application/pdf";
  const resolvedBinaryFile =
    binaryFile && !resolvedVideoFile && !resolvedAudioFile;
  const hasSupportedPreview =
    resolvedImageFile ||
    resolvedVideoFile ||
    resolvedAudioFile ||
    resolvedPdfFile ||
    cadFile ||
    pptFile ||
    htmlFile ||
    wordPreviewFile ||
    xlsxPreviewFile ||
    csvFile ||
    excalidrawFile ||
    markdownFile ||
    codeFile;
  const unsupportedPreviewFile = !hasSupportedPreview && !resolvedBinaryFile;
  const isCurrentData = data?.identity === previewIdentity;
  const currentData = isCurrentData ? data : null;
  const currentLoading = loading || (!!data && !isCurrentData);

  // Memoized values
  const language = useMemo(() => detectLanguage(fileName), [fileName]);

  const hasTextContent = useMemo(() => {
    return !!(
      currentData?.content &&
      !unsupportedPreviewFile &&
      !resolvedBinaryFile &&
      !xlsxPreviewFile &&
      !pptFile &&
      !cadFile &&
      !htmlFile &&
      !excalidrawFile
    );
  }, [
    currentData?.content,
    unsupportedPreviewFile,
    resolvedBinaryFile,
    xlsxPreviewFile,
    pptFile,
    cadFile,
    htmlFile,
    excalidrawFile,
  ]);

  const displaySize = useMemo(() => {
    if (!hasTextContent && fileSize) {
      return fileSize;
    }
    return currentData?.content?.length || 0;
  }, [currentData?.content, hasTextContent, fileSize]);

  // Content loading
  useEffect(() => {
    setLoading(true);
    setError(null);
    setData(null);
    setImageUrl(null);
    setPdfUrl(null);
    setPptUrl(null);
    setPptxBuffer(null);
    setCadUrl(null);
    setCadKind(null);
    setHtmlUrl(null);
    setHtmlContent("");
    setVideoUrl(null);
    setAudioUrl(null);
    setArrayBuffer(null);
    setExcalidrawData("");
    setResolvedUrl(null);
    let cancelled = false;
    const setCurrentData = (nextContent: string) => {
      if (!cancelled) {
        setData({ content: nextContent, path, identity: previewIdentity });
      }
    };

    const loadContent = async () => {
      if (externalImageUrl) {
        try {
          assertSafeDocumentPreviewUrl(externalImageUrl);
          setImageUrl(externalImageUrl);
          setCurrentData("");
          setLoading(false);
        } catch (err) {
          console.error("Failed to load external image preview:", err);
          setError(t("documents.failedToLoadFromS3", "从存储加载文件失败"));
          setLoading(false);
        }
        return;
      }

      if (content !== undefined) {
        if (unsupportedPreviewFile) {
          setCurrentData(content);
          setLoading(false);
          return;
        }

        if (dxfFile) {
          const blob = new Blob([content], { type: "text/plain" });
          const url = URL.createObjectURL(blob);
          setCadUrl(url);
          setCadKind("dxf");
          setCurrentData("");
          setLoading(false);
          return;
        }

        if (dwgFile) {
          setCadKind("dwg");
          setCurrentData("");
          setLoading(false);
          return;
        }

        if (htmlFile) {
          const blob = new Blob([content], { type: "text/html" });
          const url = URL.createObjectURL(blob);
          setHtmlUrl(url);
          setHtmlContent(content);
          setCurrentData("");
        } else {
          setCurrentData(content);
        }
        setLoading(false);
        return;
      }

      if (s3Key || previewUrl || signedUrl) {
        try {
          const url =
            xlsxPreviewUrl ||
            previewUrl ||
            signedUrl ||
            (s3Key ? await uploadApi.getSignedUrl(s3Key) : null);

          if (!url) {
            throw new Error("No URL available");
          }

          assertSafeDocumentPreviewUrl(url);
          setResolvedUrl(url);

          if (resolvedImageFile) {
            setImageUrl(
              await resolveDocumentPreviewUrl({
                url,
                mimeType: mimeType || mime || "application/octet-stream",
              }),
            );
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (resolvedPdfFile) {
            const buffer = await fetchDocumentArrayBuffer(url);
            const blob = new Blob([buffer], { type: "application/pdf" });
            const previewUrl = URL.createObjectURL(blob);
            setPdfUrl(previewUrl);
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (resolvedVideoFile) {
            setVideoUrl(
              await resolveDocumentPreviewUrl({
                url,
                mimeType: mimeType || mime || "video/mp4",
              }),
            );
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (resolvedAudioFile) {
            setAudioUrl(
              await resolveDocumentPreviewUrl({
                url,
                mimeType: mimeType || mime || "audio/mpeg",
              }),
            );
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (cadFile) {
            setCadUrl(
              await resolveDocumentPreviewUrl({
                url,
                mimeType: mimeType || mime || "application/octet-stream",
              }),
            );
            setCadKind(dxfFile ? "dxf" : "dwg");
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (pptFile) {
            const buffer = await resolvePptPreviewBuffer({ url });
            setPptxBuffer(buffer);
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (htmlFile) {
            setHtmlUrl(url);
            try {
              const text = await fetchDocumentText(url);
              setHtmlContent(text);
            } catch (e) {
              console.error("Failed to fetch HTML content:", e);
            }
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (excalidrawFile) {
            const text = await fetchDocumentText(url);
            setExcalidrawData(text);
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (legacyDocFile) {
            setDocUrl(
              await resolveDocumentPreviewUrl({
                url,
                mimeType: mimeType || mime || "application/msword",
              }),
            );
            setCurrentData("");
            setLoading(false);
            return;
          }

          if (resolvedBinaryFile) {
            setCurrentData("");
          } else if (unsupportedPreviewFile) {
            setCurrentData("");
          } else if (wordPreviewFile) {
            const buffer = await fetchDocumentArrayBuffer(url);
            setArrayBuffer(buffer);
            setCurrentData("");
          } else if (xlsxPreviewFile) {
            const previewJson = await fetchXlsxPreviewJson(url);
            if (cancelled) return;
            setCurrentData(previewJson);
            setLoading(false);
            return;
          } else {
            const text = await fetchDocumentText(url);
            setCurrentData(text);
          }
          setLoading(false);
        } catch (err) {
          console.error("Failed to load file from S3:", err);
          if (!cancelled) {
            setError(t("documents.failedToLoadFromS3", "从存储加载文件失败"));
            setLoading(false);
          }
        }
        return;
      }

      setError(t("documents.noContent", "文件内容不可用"));
      setLoading(false);
    };

    loadContent();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, content, s3Key, previewUrl, signedUrl, externalImageUrl, mimeType]);

  // Blob URL cleanup
  useEffect(() => {
    return () => {
      if (htmlUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(htmlUrl);
      }
      if (cadUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(cadUrl);
      }
      if (pdfUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(pdfUrl);
      }
      if (imageUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(imageUrl);
      }
      if (videoUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(videoUrl);
      }
      if (audioUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(audioUrl);
      }
      if (docUrl?.startsWith("blob:")) {
        URL.revokeObjectURL(docUrl);
      }
    };
  }, [audioUrl, cadUrl, docUrl, htmlUrl, imageUrl, pdfUrl, videoUrl]);

  // Action handlers
  const handleCopy = async () => {
    if (currentData?.content) {
      await copyToClipboard(currentData.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  const handleDownload = async () => {
    const resolvedDownloadUrl =
      downloadUrl ||
      (isExactXlsxPreviewUrl(signedUrl) ? undefined : signedUrl) ||
      (!xlsxPreviewFile ? resolvedUrl : undefined) ||
      externalImageUrl;
    if (resolvedDownloadUrl) {
      await downloadPreviewUrl({ url: resolvedDownloadUrl, fileName });
      return;
    }

    if (currentData?.content) {
      const blob = new Blob([currentData.content], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  };

  // Computed values
  const fileInfo = getFileTypeInfo(fileName, mimeType);
  const Icon = fileInfo.icon;
  const isSidebar = viewMode === "sidebar";
  const centerPanelClass = `overflow-hidden shadow-2xl ring-1 ring-black/5 dark:ring-white/10 transition-all duration-300 ease-out w-full flex flex-col bg-[var(--theme-bg-card)] pointer-events-auto relative ${
    isFullscreen
      ? "h-full sm:h-full sm:max-w-none sm:rounded-none"
      : "sm:max-w-3xl lg:max-w-4xl xl:max-w-5xl h-full sm:h-[80vh] sm:rounded-2xl"
  }`;

  return {
    // Props
    path,
    initialLine: props.initialLine,
    onClose: props.onClose,
    onUserInteraction: props.onUserInteraction,
    registryKey: props.registryKey,
    mobileFillViewport: props.mobileFillViewport,
    s3Key,
    signedUrl,
    previewUrl,
    externalImageUrl,

    // Translation
    t,

    // Data state
    data: currentData,
    loading: currentLoading,
    error,
    copied,
    imageUrl,
    pdfUrl,
    pptUrl,
    pptxBuffer,
    cadUrl,
    cadKind,
    htmlUrl,
    htmlContent,
    videoUrl,
    audioUrl,
    docUrl,
    arrayBuffer,
    excalidrawData,
    resolvedUrl,
    showImageViewer,
    viewSource,
    viewMode,
    isFullscreen,
    isMobile,
    toolbarCompact,

    // File type flags
    fileName,
    ext,
    codeFile,
    markdownFile,
    htmlFile,
    cadFile,
    pptFile,
    xlsxPreviewFile,
    wordPreviewFile,
    legacyDocFile,
    excalidrawFile,
    resolvedImageFile,
    resolvedVideoFile,
    resolvedAudioFile,
    resolvedPdfFile,
    resolvedBinaryFile,
    unsupportedPreviewFile,
    hasTextContent,

    // Computed
    language,
    previewIdentity,
    displaySize,
    fileInfo,
    Icon,
    isSidebar,
    centerPanelClass,

    // Handlers
    effectiveOnBack,
    handleCopy,
    handleDownload,

    // Setters
    setShowImageViewer,
    setViewSource,
    setViewMode,
    setIsFullscreen,

    // Refs
    toolbarRef,

    // Props for conditional checks
    fileSize,
  };
}

export type DocumentPreviewState = ReturnType<typeof useDocumentPreviewState>;
