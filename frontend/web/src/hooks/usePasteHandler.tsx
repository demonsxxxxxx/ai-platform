import { useCallback } from "react";
import toast from "react-hot-toast";
import { FileText } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  turndown,
  cleanPastedHtml,
} from "../components/chat/chatInputTurndown";
import { PASTE_TEXT_THRESHOLD } from "../components/chat/chatInputConstants";
import type { FileCategory } from "../types";

export interface UsePasteHandlerOptions {
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  input: string;
  setInput: (value: string) => void;
  uploadFiles: (files: FileList | File[], category?: FileCategory) => void;
  validateCount: (count: number) => boolean;
  scheduleTextareaResize: () => void;
}

export function usePasteHandler({
  textareaRef,
  input,
  setInput,
  uploadFiles,
  validateCount,
  scheduleTextareaResize,
}: UsePasteHandlerOptions) {
  const { t } = useTranslation();

  const textAsFile = useCallback(
    (text: string, mimeType: string, ext: string) => {
      if (!validateCount(1)) return;
      const now = new Date();
      const ts = [
        now.getFullYear(),
        String(now.getMonth() + 1).padStart(2, "0"),
        String(now.getDate()).padStart(2, "0"),
        String(now.getHours()).padStart(2, "0"),
        String(now.getMinutes()).padStart(2, "0"),
        String(now.getSeconds()).padStart(2, "0"),
      ].join("");
      const name = `clipboard-${ts}.${ext}`;
      const file = new File([text], name, { type: mimeType });
      uploadFiles([file], "document");
      toast.custom(() => (
        <div
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium"
          style={{
            background:
              "color-mix(in srgb, var(--theme-primary) 10%, transparent)",
            border:
              "1px solid color-mix(in srgb, var(--theme-primary) 20%, transparent)",
            color: "var(--theme-primary)",
          }}
        >
          <FileText size={16} className="shrink-0" />
          <span>{t("chat.textAutoUploaded", "长文本已自动转为文件上传")}</span>
        </div>
      ));
    },
    [validateCount, uploadFiles, t],
  );

  const handlePaste = useCallback(
    (e: React.ClipboardEvent) => {
      const clipboardData = e.clipboardData;
      if (!clipboardData) return;

      if (clipboardData.files && clipboardData.files.length > 0) {
        e.preventDefault();
        if (!validateCount(clipboardData.files.length)) return;
        uploadFiles(clipboardData.files);
        return;
      }

      const htmlText = clipboardData.getData("text/html");
      if (htmlText) {
        e.preventDefault();
        const tempDiv = document.createElement("div");
        tempDiv.innerHTML = htmlText;
        cleanPastedHtml(tempDiv);
        const markdownText = turndown.turndown(tempDiv);

        if (markdownText.length > PASTE_TEXT_THRESHOLD) {
          textAsFile(markdownText, "text/markdown", "md");
          return;
        }

        const textarea = textareaRef.current;
        if (textarea) {
          const start = textarea.selectionStart;
          const end = textarea.selectionEnd;
          const newValue =
            input.substring(0, start) + markdownText + input.substring(end);
          setInput(newValue);
          setTimeout(() => {
            textarea.selectionStart = textarea.selectionEnd =
              start + markdownText.length;
            textarea.focus();
            scheduleTextareaResize();
          }, 0);
        }
        return;
      }

      const plainText = clipboardData.getData("text/plain");
      if (plainText && plainText.length > PASTE_TEXT_THRESHOLD) {
        e.preventDefault();
        textAsFile(plainText, "text/plain", "txt");
      }
    },
    [
      textareaRef,
      input,
      setInput,
      uploadFiles,
      validateCount,
      textAsFile,
      scheduleTextareaResize,
    ],
  );

  return { handlePaste };
}
