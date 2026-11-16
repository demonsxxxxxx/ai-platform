import { Suspense, lazy } from "react";
import { AlertCircle } from "lucide-react";
import { Spin } from "@lobehub/ui/base-ui";
import { ImageViewer } from "../common/ImageViewer";
import CodeRenderer from "./previews/CodeRenderer";
import MarkdownRenderer from "./previews/MarkdownRenderer";
import HtmlPreview from "./previews/HtmlPreview";

const PdfPreview = lazy(() => import("./previews/PdfPreview"));
const PptPreview = lazy(() => import("./previews/PptPreview"));
const WordPreview = lazy(() => import("./previews/WordPreview"));
const ExcelPreview = lazy(() => import("./previews/ExcelPreview"));
const ExcalidrawPreview = lazy(() => import("./previews/ExcalidrawPreview"));
const CadPreview = lazy(() => import("./previews/CadPreview"));
const FileFallbackPanel = lazy(() => import("./previews/FileFallbackPanel"));

import type { DocumentPreviewState } from "./useDocumentPreviewState";

type ContentProps = Pick<
  DocumentPreviewState,
  | "t"
  | "data"
  | "loading"
  | "error"
  | "imageUrl"
  | "pdfUrl"
  | "pptUrl"
  | "pptxBuffer"
  | "cadUrl"
  | "cadKind"
  | "htmlUrl"
  | "htmlContent"
  | "videoUrl"
  | "audioUrl"
  | "docUrl"
  | "arrayBuffer"
  | "excalidrawData"
  | "showImageViewer"
  | "viewSource"
  | "path"
  | "initialLine"
  | "language"
  | "ext"
  | "fileName"
  | "fileInfo"
  | "Icon"
  | "markdownFile"
  | "htmlFile"
  | "pptFile"
  | "xlsxPreviewFile"
  | "previewIdentity"
  | "wordPreviewFile"
  | "legacyDocFile"
  | "excalidrawFile"
  | "resolvedImageFile"
  | "resolvedVideoFile"
  | "resolvedAudioFile"
  | "resolvedPdfFile"
  | "resolvedBinaryFile"
  | "unsupportedPreviewFile"
  | "handleDownload"
  | "setShowImageViewer"
>;

export default function DocumentPreviewContent({
  t,
  data,
  loading,
  error,
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
  showImageViewer,
  viewSource,
  path,
  initialLine,
  language,
  ext,
  fileName,
  fileInfo,
  Icon,
  markdownFile,
  htmlFile,
  pptFile,
  xlsxPreviewFile,
  previewIdentity,
  wordPreviewFile,
  legacyDocFile,
  excalidrawFile,
  resolvedImageFile,
  resolvedVideoFile,
  resolvedAudioFile,
  resolvedPdfFile,
  resolvedBinaryFile,
  unsupportedPreviewFile,
  handleDownload,
  setShowImageViewer,
}: ContentProps) {
  const suspenseFallback = (
    <div className="flex items-center justify-center h-full min-h-[400px]">
      <Spin size={32} />
    </div>
  );

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-16 sm:py-20 gap-4">
        <div className="relative">
          <Spin size={32} />

        </div>
        <p className="text-sm text-neutral-500 dark:text-neutral-400 font-medium">
          {t("documents.loadingFileContent")}
        </p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-16 sm:py-20 gap-4 px-4">
        <div className="flex items-center justify-center w-16 h-16 rounded-2xl bg-[var(--theme-danger-soft)]">
          <AlertCircle size={28} className="text-[var(--theme-danger-text)]" />
        </div>
        <div className="text-center">
          <p className="text-sm text-[var(--theme-danger-text)] font-medium mb-2">
            {error}
          </p>
          <p className="text-xs text-neutral-400 dark:text-neutral-500">
            {t("documents.unableToLoadContent")}
          </p>
        </div>
      </div>
    );
  }

  if (
    resolvedBinaryFile &&
    !resolvedImageFile &&
    !resolvedPdfFile &&
    !resolvedVideoFile &&
    !resolvedAudioFile
  ) {
    return (
      <Suspense fallback={suspenseFallback}>
        <FileFallbackPanel
          icon={Icon}
          iconBg={fileInfo.bg}
          iconColor={fileInfo.color}
          title={t("documents.binaryFilePreview")}
          description={t("documents.binaryFileHint")}
          fileName={fileName}
          downloadLabel={t("documents.downloadFile")}
          onDownload={handleDownload}
        />
      </Suspense>
    );
  }

  if (unsupportedPreviewFile) {
    return (
      <Suspense fallback={suspenseFallback}>
        <FileFallbackPanel
          icon={Icon}
          iconBg={fileInfo.bg}
          iconColor={fileInfo.color}
          title={t("documents.unsupportedFilePreview", "暂不支持预览此文件")}
          description={t(
            "documents.unsupportedFileHint",
            "此文件类型暂不支持在线预览，请下载后查看。",
          )}
          fileName={fileName}
          downloadLabel={t("documents.downloadFile")}
          onDownload={handleDownload}
        />
      </Suspense>
    );
  }

  if (resolvedPdfFile) {
    return (
      <Suspense fallback={suspenseFallback}>
        <div className="h-full min-h-[400px]">
          {pdfUrl && <PdfPreview url={pdfUrl} />}
        </div>
      </Suspense>
    );
  }

  if (resolvedVideoFile && videoUrl) {
    return (
      <div className="flex items-center justify-center h-full bg-gradient-to-b from-neutral-900 to-neutral-950 min-h-[400px] p-4 sm:p-8">
        <div className="relative w-full max-w-4xl mx-auto">
          <video
            controls
            autoPlay={false}
            className="w-full max-h-[65vh] rounded-xl shadow-2xl ring-1 ring-[color-mix(in_srgb,var(--theme-on-overlay)_10%,transparent)]"
            src={videoUrl}
            style={{ margin: "0 auto", display: "block" }}
          >
            <track kind="captions" />
            {t("documents.videoNotSupported")}
          </video>
        </div>
      </div>
    );
  }

  if (resolvedAudioFile && audioUrl) {
    return (
      <div className="flex items-center justify-center h-full min-h-[400px] p-4 sm:p-8">
        <div className="w-full max-w-lg mx-auto flex flex-col items-center gap-6">
          <div
            className={`flex items-center justify-center w-20 h-20 rounded-2xl ${fileInfo.bg}`}
          >
            <Icon size={36} className={fileInfo.color} />
          </div>
          <audio controls className="w-full" src={audioUrl}>
            {t("documents.audioNotSupported", "您的浏览器不支持音频播放")}
          </audio>
        </div>
      </div>
    );
  }

  if (cadKind) {
    return (
      <Suspense fallback={suspenseFallback}>
        <div className="h-full min-h-[400px]">
          <CadPreview fileName={fileName} kind={cadKind} url={cadUrl} t={t} />
        </div>
      </Suspense>
    );
  }

  if (pptFile && (pptUrl || pptxBuffer)) {
    return (
      <Suspense fallback={suspenseFallback}>
        <div className="h-full min-h-[400px]">
          <PptPreview
            url={pptUrl || ""}
            arrayBuffer={pptxBuffer}
            fileName={fileName}
            t={t}
            onDownload={handleDownload}
          />
        </div>
      </Suspense>
    );
  }

  if (htmlFile && htmlUrl) {
    return (
      <div className="h-full min-h-[400px]">
        <HtmlPreview content={htmlContent} />
      </div>
    );
  }

  if (legacyDocFile && docUrl) {
    return (
      <Suspense fallback={suspenseFallback}>
        <FileFallbackPanel
          icon={Icon}
          iconBg="bg-[var(--theme-info-soft)]"
          iconColor="text-[var(--theme-info-text)]"
          title={t("documents.docNotSupported") || "不支持预览旧版 Word 文档"}
          description={
            t("documents.docConvertHint") ||
            "该文件为旧版 .doc 格式，请将其转换为 .docx 格式后预览，或直接下载文件。"
          }
          fileName={fileName}
          downloadLabel={t("documents.download") || "下载文件"}
          onDownload={handleDownload}
        />
      </Suspense>
    );
  }

  if (wordPreviewFile && arrayBuffer) {
    return (
      <Suspense fallback={suspenseFallback}>
        <WordPreview arrayBuffer={arrayBuffer} t={t} />
      </Suspense>
    );
  }

  if (xlsxPreviewFile && data?.content) {
    return (
      <Suspense fallback={suspenseFallback}>
        <ExcelPreview key={previewIdentity} previewJson={data.content} t={t} />
      </Suspense>
    );
  }

  if (resolvedImageFile || imageUrl) {
    return (
      <>
        <div className="flex items-center justify-center p-4 sm:p-8 bg-neutral-50 dark:bg-neutral-800/50 h-full overflow-auto">
          <img
            src={imageUrl || `data:image/${ext};base64,${data?.content}`}
            alt={fileName}
            className="rounded-lg shadow-lg object-contain cursor-pointer hover:opacity-90 transition-opacity max-w-full max-h-full"
            onClick={(e) => {
              e.stopPropagation();
              setShowImageViewer(true);
            }}
          />
        </div>
        {showImageViewer && (
          <ImageViewer
            isOpen={showImageViewer}
            src={imageUrl || `data:image/${ext};base64,${data?.content}`}
            onClose={() => setShowImageViewer(false)}
          />
        )}
      </>
    );
  }

  if (excalidrawFile && excalidrawData) {
    return (
      <Suspense fallback={suspenseFallback}>
        <div className="h-full min-h-[400px] max-h-full overflow-hidden">
          <ExcalidrawPreview data={excalidrawData} />
        </div>
      </Suspense>
    );
  }

  if (markdownFile) {
    return viewSource ? (
      <CodeRenderer content={data?.content || ""} filePath={path} t={t} />
    ) : (
      <MarkdownRenderer content={data?.content || ""} _t={t} />
    );
  }

  return (
    <CodeRenderer
      content={data?.content || ""}
      language={language}
      t={t}
      initialLine={initialLine}
    />
  );
}
