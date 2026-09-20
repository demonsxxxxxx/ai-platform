import { memo, useEffect, useState } from "react";
import { FileWarning } from "lucide-react";
import { pptxToHtml } from "@jvmr/pptx-to-html";
import JSZip from "jszip";
import type { TFunction } from "i18next";
import FileFallbackPanel from "./FileFallbackPanel";
import { preparePptxSlideDocument } from "./pptHtmlPreview";
import {
  DocumentViewerFrame,
  ScaledDocumentContent,
} from "./DocumentViewerFrame";

interface PptPreviewProps {
  url: string;
  arrayBuffer?: ArrayBuffer | null;
  fileName: string;
  t: TFunction;
  onDownload?: () => void;
}

const PPT_PREVIEW_WIDTH = 960;
const PPT_PREVIEW_HEIGHT = 540;
const PPT_SLIDE_GAP = 20;

const PptPreview = memo(function PptPreview({
  url,
  arrayBuffer,
  fileName,
  t,
  onDownload,
}: PptPreviewProps) {
  const [visualSlides, setVisualSlides] = useState<string[]>([]);
  const [textSlides, setTextSlides] = useState<PptSlidePreview[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setVisualSlides([]);
    setTextSlides([]);
    setLoading(true);
    setLoadFailed(false);

    if (!arrayBuffer) {
      setLoading(false);
      setLoadFailed(true);
      return;
    }

    const renderPresentation = async () => {
      try {
        const slides = await pptxToHtml(arrayBuffer.slice(0), {
          width: PPT_PREVIEW_WIDTH,
          height: PPT_PREVIEW_HEIGHT,
          scaleToFit: true,
          letterbox: true,
        });
        if (cancelled) return;
        if (slides.length > 0) {
          setVisualSlides(slides.map(preparePptxSlideDocument));
          setLoading(false);
          return;
        }
      } catch (error) {
        console.warn("Failed to render visual PPTX preview:", error);
      }

      try {
        const slides = await extractPptxSlides(arrayBuffer.slice(0));
        if (cancelled) return;
        setTextSlides(slides);
        setLoadFailed(slides.length === 0);
      } catch (error) {
        console.warn("Failed to extract PPTX text preview:", error);
        if (!cancelled) setLoadFailed(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void renderPresentation();
    return () => {
      cancelled = true;
    };
  }, [arrayBuffer]);

  if (!loading && textSlides.length > 0) {
    return (
      <div className="h-full min-h-[400px] overflow-auto bg-stone-50 p-4 dark:bg-stone-950/40">
        <div className="mx-auto max-w-4xl space-y-3">
          <div className="rounded-lg border border-stone-200 bg-white px-4 py-3 shadow-sm dark:border-stone-800 dark:bg-stone-900">
            <div className="text-sm font-semibold text-stone-800 dark:text-stone-100">
              {fileName}
            </div>
            <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">
              {t(
                "documents.pptPreviewTextMode",
                "已使用安全文本模式加载 PowerPoint。",
              )}
            </div>
          </div>
          {textSlides.map((slide) => (
            <section
              key={slide.id}
              className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900"
            >
              <h3 className="text-xs font-semibold uppercase text-stone-400 dark:text-stone-500">
                {t("documents.pptSlideLabel", "幻灯片 {{count}}", {
                  count: slide.number,
                })}
              </h3>
              <div className="mt-3 space-y-1 text-sm leading-6 text-stone-700 dark:text-stone-200">
                {slide.text.map((line, index) => (
                  <p key={`${slide.id}:${index}`}>{line}</p>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    );
  }

  if (loadFailed) {
    return (
      <FileFallbackPanel
        icon={FileWarning}
        iconBg="bg-amber-100 dark:bg-amber-900/40"
        iconColor="text-amber-600 dark:text-amber-300"
        title={t("documents.pptPreviewUnavailable", "PPT 预览不可用")}
        description={t(
          "documents.pptPreviewUnavailableHint",
          "当前浏览器无法直接渲染这个演示文稿。旧版 .ppt 或复杂版式可能需要下载后用 PowerPoint、WPS 或 Keynote 打开。",
        )}
        downloadUrl={url || undefined}
        fileName={fileName}
        downloadLabel={t("documents.downloadFile")}
        onDownload={onDownload}
      />
    );
  }

  const contentHeight = Math.max(
    PPT_PREVIEW_HEIGHT,
    visualSlides.length * PPT_PREVIEW_HEIGHT +
      Math.max(0, visualSlides.length - 1) * PPT_SLIDE_GAP,
  );

  return (
    <DocumentViewerFrame
      naturalWidth={PPT_PREVIEW_WIDTH}
      loading={loading}
      ariaLabel={t("documents.pptPreviewTitle", "PowerPoint 预览")}
    >
      {(displayScale) => (
        <ScaledDocumentContent
          naturalWidth={PPT_PREVIEW_WIDTH}
          naturalHeight={contentHeight}
          displayScale={displayScale}
          className="flex flex-col gap-5"
        >
          {visualSlides.map((slideDocument, index) => (
            <iframe
              key={index}
              srcDoc={slideDocument}
              title={t("documents.pptSlideLabel", "幻灯片 {{count}}", {
                count: index + 1,
              })}
              sandbox=""
              referrerPolicy="no-referrer"
              className="shrink-0 border-0 bg-white shadow-xl ring-1 ring-black/5"
              style={{
                width: PPT_PREVIEW_WIDTH,
                height: PPT_PREVIEW_HEIGHT,
              }}
            />
          ))}
        </ScaledDocumentContent>
      )}
    </DocumentViewerFrame>
  );
});

export default PptPreview;

interface PptSlidePreview {
  id: string;
  number: number;
  text: string[];
}

function getSlideNumber(path: string): number {
  const match = path.match(/slide(\d+)\.xml$/);
  return match ? Number(match[1]) : 0;
}

function decodeXmlText(value: string): string {
  return value
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&amp;/g, "&");
}

async function extractPptxSlides(
  arrayBuffer: ArrayBuffer,
): Promise<PptSlidePreview[]> {
  const zip = await JSZip.loadAsync(arrayBuffer);
  const slidePaths = Object.keys(zip.files)
    .filter((path) => /^ppt\/slides\/slide\d+\.xml$/.test(path))
    .sort((left, right) => getSlideNumber(left) - getSlideNumber(right));

  const slides: PptSlidePreview[] = [];
  for (const path of slidePaths) {
    const file = zip.file(path);
    if (!file) continue;
    const xml = await file.async("text");
    const text = Array.from(xml.matchAll(/<a:t>([\s\S]*?)<\/a:t>/g))
      .map((match) => decodeXmlText(match[1]).trim())
      .filter(Boolean);
    const slideNumber = getSlideNumber(path);
    slides.push({
      id: path,
      number: slideNumber || slides.length + 1,
      text,
    });
  }
  return slides;
}
