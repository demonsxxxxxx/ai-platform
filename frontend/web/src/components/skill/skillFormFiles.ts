import type { SkillResponse } from "../../types/skill";
import { DEFAULT_CONTENT, type FileEntry } from "./SkillForm.types";

export function initializeSkillFormFiles(skill?: SkillResponse | null): FileEntry[] {
  if (!skill) return [{ path: "SKILL.md", content: DEFAULT_CONTENT, loaded: true, dirty: true }];
  const paths = new Set([
    ...(skill.filePaths ?? []),
    ...Object.keys(skill.files),
    ...Object.keys(skill.binaryFiles ?? {}),
  ]);
  if (skill.content) paths.add("SKILL.md");
  return [...paths].sort((a, b) => a === "SKILL.md" ? -1 : b === "SKILL.md" ? 1 : a.localeCompare(b)).map((path) => {
    const content = path === "SKILL.md" && skill.content
      ? skill.content : skill.files[path];
    const binary = Boolean(skill.binaryFiles?.[path]);
    return { path, originalPath: path, content: binary ? "" : content ?? "", loaded: binary || content !== undefined, binary, dirty: false };
  });
}

export function buildSkillFormFileChanges(files: FileEntry[], originalPaths: readonly string[], skillMarkdown: string) {
  const writes: Record<string, string> = { "SKILL.md": skillMarkdown };
  const retainedPaths = new Set<string>();
  for (const file of files) {
    const path = file.path.trim();
    if (!path) continue;
    retainedPaths.add(path);
    if (path !== "SKILL.md" && file.loaded !== false && !file.binary && (file.dirty || file.originalPath !== path)) {
      writes[path] = file.content;
    }
  }
  return { files: writes, deletedFiles: originalPaths.filter((path) => !retainedPaths.has(path)) };
}
