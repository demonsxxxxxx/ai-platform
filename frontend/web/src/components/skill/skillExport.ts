import type { SkillFileResponse } from "../../types/skill";

/** Decode the authenticated file API, never a separate third-party URL. */
export function skillExportContent(file: SkillFileResponse): string | Uint8Array {
  if (typeof file.content !== "string") throw new Error("skill_file_content_invalid");
  if (!file.is_binary) return file.content;
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(file.content)) {
    throw new Error("skill_file_content_invalid");
  }
  const decoded = atob(file.content);
  if (btoa(decoded) !== file.content) throw new Error("skill_file_content_invalid");
  return Uint8Array.from(decoded, (character) => character.charCodeAt(0));
}
