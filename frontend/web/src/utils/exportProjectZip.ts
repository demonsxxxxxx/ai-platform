import JSZip from "jszip";

export async function exportProjectZip(
  files: Record<string, string | Uint8Array>,
  projectName: string,
  binaryFiles?: Record<string, string>,
  isCurrent: () => boolean = () => true,
): Promise<void> {
  if (!isCurrent()) throw new DOMException("Export cancelled", "AbortError");
  const zip = new JSZip();

  // 添加文本文件
  for (const [path, content] of Object.entries(files)) {
    const normalizedPath = path.startsWith("/") ? path.slice(1) : path;
    if (normalizedPath) {
      zip.file(normalizedPath, content);
    }
  }

  // 添加二进制文件（从 OSS URL 拉取）
  if (binaryFiles) {
    await Promise.all(
      Object.entries(binaryFiles).map(async ([path, url]) => {
        try {
          const resp = await fetch(url);
          if (!resp.ok) return;
          const buffer = await resp.arrayBuffer();
          const normalizedPath = path.startsWith("/") ? path.slice(1) : path;
          if (normalizedPath) {
            zip.file(normalizedPath, buffer);
          }
        } catch {
          // 跳过下载失败的二进制文件
        }
      }),
    );
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
