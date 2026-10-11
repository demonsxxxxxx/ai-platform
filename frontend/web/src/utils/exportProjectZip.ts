import JSZip from "jszip";

export async function exportProjectZip(
  files: Record<string, string | Uint8Array>,
  projectName: string,
  isCurrent: () => boolean = () => true,
): Promise<void> {
  if (!isCurrent()) throw new DOMException("Export cancelled", "AbortError");
  const zip = new JSZip();

  // Add the already-authorized file bytes without fetching secondary URLs.
  for (const [path, content] of Object.entries(files)) {
    const normalizedPath = path.startsWith("/") ? path.slice(1) : path;
    if (normalizedPath) {
      zip.file(normalizedPath, content);
    }
  }

  const blob = await zip.generateAsync({ type: "blob" });
  if (!isCurrent()) throw new DOMException("Export cancelled", "AbortError");
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${projectName.replace(
    /[^a-zA-Z0-9_\u4e00-\u9fa5-]/g,
    "_",
  )}.zip`;
  try {
    document.body.appendChild(a);
    a.click();
  } finally {
    a.remove();
    URL.revokeObjectURL(url);
  }
}
