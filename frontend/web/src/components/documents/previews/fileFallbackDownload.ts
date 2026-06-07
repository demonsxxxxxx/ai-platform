export interface FileFallbackDownloadInput {
  onDownload?: () => void;
  downloadUrl?: string | null;
  fileName: string;
  documentRef?: Document;
}

export function runFileFallbackDownload(
  input: FileFallbackDownloadInput,
): void {
  if (input.onDownload) {
    input.onDownload();
    return;
  }
  if (!input.downloadUrl) return;
  const documentRef =
    input.documentRef ?? (typeof document !== "undefined" ? document : null);
  if (!documentRef) return;
  const anchor = documentRef.createElement("a");
  anchor.href = input.downloadUrl;
  anchor.download = input.fileName;
  documentRef.body.appendChild(anchor);
  anchor.click();
  documentRef.body.removeChild(anchor);
}
