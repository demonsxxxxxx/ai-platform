import { memo, useEffect, useState } from "react";
import JSZip from "jszip";
import type { TFunction } from "i18next";

interface PptPreviewProps {
  url: string;
  arrayBuffer?: ArrayBuffer | null;
  fileName: string;
  t: TFunction;
}

const PptPreview = memo(function PptPreview({
  arrayBuffer,
  fileName,
  t,
}: PptPreviewProps) {
  const [slides, setSlides] = useState<PptSlidePreview[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!arrayBuffer) {
      setSlides(null);
      setError(null);
      return;
    }

    let cancelled = false;
    setSlides(null);
    setError(null);

    void extractPptxSlides(arrayBuffer)
      .then((nextSlides) => {
        if (!cancelled) {
          setSlides(nextSlides);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError(
            t("documents.pptPreviewFallback", "Presentation loaded safely."),
          );
          setSlides([]);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [arrayBuffer, t]);

  if (arrayBuffer) {
    if (!slides && !error) {
      return (
        <div className="flex h-full min-h-[400px] items-center justify-center bg-stone-50 p-6 text-sm text-stone-500 dark:bg-stone-900/40 dark:text-stone-400">
          {t("documents.loadingFileContent")}
        </div>
      );
    }

    return (
      <div className="h-full min-h-[400px] overflow-auto bg-stone-50 p-4 dark:bg-stone-950/40">
        <div className="mx-auto max-w-4xl space-y-3">
          <div className="rounded-lg border border-stone-200 bg-white px-4 py-3 shadow-sm dark:border-stone-800 dark:bg-stone-900">
            <div className="text-sm font-semibold text-stone-800 dark:text-stone-100">
              {fileName}
            </div>
            <div className="mt-1 text-xs text-stone-500 dark:text-stone-400">
              {error ||
                t(
                  "documents.pptPreviewTextMode",
                  "PowerPoint loaded through authenticated local preview.",
                )}
            </div>
          </div>
          {slides && slides.length > 0 ? (
            slides.map((slide) => (
              <section
                key={slide.id}
                className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm dark:border-stone-800 dark:bg-stone-900"
              >
                <h3 className="text-xs font-semibold uppercase text-stone-400 dark:text-stone-500">
                  {slide.title}
                </h3>
                {slide.text.length > 0 ? (
                  <div className="mt-3 space-y-1 text-sm leading-6 text-stone-700 dark:text-stone-200">
                    {slide.text.map((line, index) => (
                      <p key={`${slide.id}:${index}`}>{line}</p>
                    ))}
                  </div>
                ) : (
                  <p className="mt-3 text-sm text-stone-500 dark:text-stone-400">
                    {t("documents.noTextContent", "没有可提取的文本内容")}
                  </p>
                )}
              </section>
            ))
          ) : (
            <div className="rounded-lg border border-stone-200 bg-white px-4 py-6 text-center text-sm text-stone-500 shadow-sm dark:border-stone-800 dark:bg-stone-900 dark:text-stone-400">
              {t(
                "documents.pptPreviewNoSlides",
                "无法提取幻灯片文字。请下载文件查看完整演示文稿。",
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-[400px] items-center justify-center bg-stone-50 p-6 text-center text-sm text-stone-500 dark:bg-stone-900/40 dark:text-stone-400">
      {t(
        "documents.pptPreviewNoData",
        "没有可用于预览的本地演示数据。",
      )}
    </div>
  );
});

export default PptPreview;

interface PptSlidePreview {
  id: string;
  title: string;
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
      title: `Slide ${slideNumber || slides.length + 1}`,
      text,
    });
  }
  return slides;
}
