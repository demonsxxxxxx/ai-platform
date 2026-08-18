import { useEffect, useRef, useState } from "react";
import { useFileUpload } from "../../../hooks/useFileUpload";
import type { MessageAttachment } from "../../../types";
import { shouldHandleGlobalFileDrop } from "./globalFileDropGuards";

export function useDragAndDrop(acceptedFileTypes?: readonly string[]) {
  const [isPageDragging, setIsPageDragging] = useState(false);
  const [pageDragAttachments, setPageDragAttachments] = useState<
    MessageAttachment[]
  >([]);

  const { uploadFiles, validateCount } = useFileUpload({
    attachments: pageDragAttachments,
    onAttachmentsChange: setPageDragAttachments,
    acceptedFileTypes,
  });
  const uploadsEnabled = acceptedFileTypes === undefined || acceptedFileTypes.length > 0;

  const dragCounterRef = useRef(0);

  useEffect(() => {
    const resetDragState = () => {
      dragCounterRef.current = 0;
      setIsPageDragging(false);
    };

    const handleDragEnter = (e: DragEvent) => {
      if (!uploadsEnabled) return;
      if (e.dataTransfer?.types.includes("Files")) {
        if (!shouldHandleGlobalFileDrop(e)) {
          resetDragState();
          return;
        }
        e.preventDefault();
        dragCounterRef.current++;
        if (dragCounterRef.current === 1) {
          setIsPageDragging(true);
        }
      }
    };

    const handleDragLeave = (e: DragEvent) => {
      if (e.dataTransfer?.types.includes("Files")) {
        if (!shouldHandleGlobalFileDrop(e)) {
          resetDragState();
          return;
        }
        dragCounterRef.current--;
        if (dragCounterRef.current <= 0) {
          resetDragState();
        }
      }
    };

    const handleDrop = (e: DragEvent) => {
      if (!uploadsEnabled) return;
      if (!shouldHandleGlobalFileDrop(e)) {
        resetDragState();
        return;
      }

      resetDragState();

      const files = e.dataTransfer?.files;
      if (!files || files.length === 0) return;

      e.preventDefault();

      if (!validateCount(files.length)) return;

      uploadFiles(files);
    };

    const handleDragOver = (e: DragEvent) => {
      if (!uploadsEnabled) return;
      if (e.dataTransfer?.types.includes("Files")) {
        if (!shouldHandleGlobalFileDrop(e)) {
          resetDragState();
          return;
        }
        e.preventDefault();
      }
    };

    document.addEventListener("dragenter", handleDragEnter);
    document.addEventListener("dragleave", handleDragLeave);
    document.addEventListener("drop", handleDrop);
    document.addEventListener("dragover", handleDragOver);

    return () => {
      document.removeEventListener("dragenter", handleDragEnter);
      document.removeEventListener("dragleave", handleDragLeave);
      document.removeEventListener("drop", handleDrop);
      document.removeEventListener("dragover", handleDragOver);
    };
  }, [uploadFiles, uploadsEnabled, validateCount]);

  return {
    isPageDragging,
    pageDragAttachments,
    setPageDragAttachments,
  };
}
