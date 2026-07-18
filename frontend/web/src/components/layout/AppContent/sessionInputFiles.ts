import type { SessionInputFile } from "../../../services/api";
import type { Message, MessageAttachment } from "../../../types";

/** Convert one public session-file projection into a previewable attachment. */
export function sessionInputFileToAttachment(
  file: SessionInputFile,
): MessageAttachment {
  const mimeType = file.mime_type.toLowerCase();
  const type: MessageAttachment["type"] = mimeType.startsWith("image/")
    ? "image"
    : mimeType.startsWith("video/")
      ? "video"
      : mimeType.startsWith("audio/")
        ? "audio"
        : "document";
  return {
    id: file.file_id,
    key: file.file_id,
    name: file.name,
    type,
    mimeType: file.mime_type,
    size: file.size_bytes,
    url: file.preview_url ?? file.download_url,
    downloadUrl: file.download_url,
  };
}

/** Hydrate persisted user cards from the authoritative run-bound projection. */
export function mergeProjectedSessionFiles(
  messages: Message[],
  files: SessionInputFile[],
): Message[] {
  if (files.length === 0) return messages;
  const filesByRun = new Map<string, SessionInputFile[]>();
  files.forEach((file) => {
    filesByRun.set(file.run_id, [...(filesByRun.get(file.run_id) ?? []), file]);
  });
  return messages.map((message) => {
    if (message.role !== "user" || !message.runId) return message;
    const runFiles = filesByRun.get(message.runId);
    if (!runFiles?.length) return message;
    const projected = runFiles.map(sessionInputFileToAttachment);
    const projectedById = new Map(projected.map((file) => [file.id, file]));
    const existing = message.attachments ?? [];
    const merged = existing.map(
      (attachment) =>
        projectedById.get(attachment.id) ??
        projectedById.get(attachment.key) ??
        attachment,
    );
    const existingIds = new Set(
      existing.flatMap((attachment) => [attachment.id, attachment.key]),
    );
    projected.forEach((attachment) => {
      if (!existingIds.has(attachment.id)) merged.push(attachment);
    });
    return { ...message, attachments: merged };
  });
}
